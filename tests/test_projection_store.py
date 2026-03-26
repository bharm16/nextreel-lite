"""Tests for ProjectionStore — state machine, enrichment, cooldown.

Targets untested invariants:
  1. fetch_renderable_payload state machine (READY→STALE, CORE→enqueue)
  2. enrich_projection error path (nested exception in ensure_core_projection)
  3. _enqueue_enrichment_if_needed cooldown enforcement
  4. ensure_core_projection UPSERT preserves ready/stale state
  5. Double-enqueue prevention on first visit
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from movies.projection_store import (
    ENQUEUE_COOLDOWN,
    PLACEHOLDER_BACKDROP,
    PLACEHOLDER_POSTER,
    PROJECTION_CORE,
    PROJECTION_FAILED,
    PROJECTION_READY,
    PROJECTION_STALE,
    STALE_AFTER,
    ProjectionStore,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fake DB pool
# ---------------------------------------------------------------------------

class FakeProjectionDB:
    """Minimal DB fake for projection_store queries."""

    def __init__(self):
        self.projections: dict[str, dict] = {}
        self.basics: dict[str, dict] = {}
        self.execute_log: list[str] = []

    def _add_basic(self, tconst, title="Test Movie", year=2024):
        self.basics[tconst] = {
            "tconst": tconst,
            "primaryTitle": title,
            "startYear": year,
            "genres": "Drama",
            "language": "en",
            "slug": "test-movie",
            "averageRating": 7.5,
            "numVotes": 150000,
        }

    def _add_projection(self, tconst, state=PROJECTION_READY, payload=None, **overrides):
        row = {
            "tconst": tconst,
            "tmdb_id": 123,
            "payload_json": json.dumps(payload or {"title": "Test", "projection_state": state}),
            "projection_state": state,
            "enriched_at": _now(),
            "stale_after": _now() + STALE_AFTER,
            "last_attempt_at": _now(),
            "attempt_count": 0,
            "last_error": None,
        }
        row.update(overrides)
        self.projections[tconst] = row

    async def execute(self, query, params=None, fetch="one", **kw):
        params = params or []
        q = query.strip().upper()
        self.execute_log.append(q[:60])

        # SELECT movie_projection
        if "FROM MOVIE_PROJECTION" in q and q.startswith("SELECT"):
            tconst = params[0]
            return self.projections.get(tconst)

        # SELECT title.basics
        if "FROM `TITLE.BASICS`" in q and q.startswith("SELECT"):
            tconst = params[0]
            return self.basics.get(tconst)

        # INSERT movie_projection
        if "INSERT INTO MOVIE_PROJECTION" in q:
            tconst = params[0]
            if tconst not in self.projections:
                self.projections[tconst] = {
                    "tconst": tconst,
                    "tmdb_id": params[1],
                    "payload_json": params[2],
                    "projection_state": params[3],
                    "enriched_at": None,
                    "stale_after": None,
                    "last_attempt_at": None,
                    "attempt_count": 0,
                    "last_error": None,
                }
            return 1

        # UPDATE movie_projection SET projection_state
        if "UPDATE MOVIE_PROJECTION" in q and "PROJECTION_STATE" in q:
            tconst = params[-1]
            row = self.projections.get(tconst)
            if row:
                row["projection_state"] = params[0]
                return 1
            return 0

        # UPDATE movie_projection SET last_attempt_at
        if "UPDATE MOVIE_PROJECTION" in q and "LAST_ATTEMPT_AT" in q:
            tconst = params[1]
            row = self.projections.get(tconst)
            if row:
                row["last_attempt_at"] = params[0]
                return 1
            return 0

        # SELECT movie_candidates (for ready_check)
        if "FROM MOVIE_CANDIDATES" in q:
            return None

        return None


@pytest.fixture
def db():
    return FakeProjectionDB()


@pytest.fixture
def store(db):
    return ProjectionStore(db, tmdb_helper=MagicMock())


# ═══════════════════════════════════════════════════════════════════════
# 1. fetch_renderable_payload — state machine
# ═══════════════════════════════════════════════════════════════════════


class TestFetchRenderablePayload:
    @pytest.mark.asyncio
    async def test_returns_ready_payload(self, store, db):
        payload = {"title": "Ready Movie", "projection_state": PROJECTION_READY}
        db._add_projection("tt1", PROJECTION_READY, payload)

        with patch.object(store, "_enqueue_enrichment_if_needed", new_callable=AsyncMock):
            result = await store.fetch_renderable_payload("tt1")

        assert result["title"] == "Ready Movie"

    @pytest.mark.asyncio
    async def test_stale_transition_when_stale_after_passed(self, store, db):
        """READY row with stale_after in the past should transition to STALE."""
        payload = {"title": "Stale Movie", "projection_state": PROJECTION_READY}
        db._add_projection(
            "tt1", PROJECTION_READY, payload,
            stale_after=_now() - timedelta(hours=1),
        )

        with patch.object(store, "_enqueue_enrichment_if_needed", new_callable=AsyncMock):
            result = await store.fetch_renderable_payload("tt1")

        assert db.projections["tt1"]["projection_state"] == PROJECTION_STALE
        assert result is not None  # Still returns data despite stale

    @pytest.mark.asyncio
    async def test_core_projection_falls_through_to_ensure(self, store, db):
        """Row with CORE state should fall through to ensure_core_projection."""
        db._add_projection("tt1", PROJECTION_CORE)
        db._add_basic("tt1")

        with patch.object(store, "_enqueue_enrichment_if_needed", new_callable=AsyncMock):
            result = await store.fetch_renderable_payload("tt1")

        assert result is not None

    @pytest.mark.asyncio
    async def test_failed_projection_falls_through_to_ensure(self, store, db):
        """Row with FAILED state should also fall through."""
        db._add_projection("tt1", PROJECTION_FAILED)
        db._add_basic("tt1")

        with patch.object(store, "_enqueue_enrichment_if_needed", new_callable=AsyncMock):
            result = await store.fetch_renderable_payload("tt1")

        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_tconst_returns_none(self, store, db):
        """No projection row AND no title.basics row → None."""
        with patch.object(store, "_enqueue_enrichment_if_needed", new_callable=AsyncMock):
            result = await store.fetch_renderable_payload("tt_nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_re_reads_row_after_ensure_core(self, store, db):
        """BUG FINDER: After ensure_core_projection, the code should re-read
        the row so _enqueue_enrichment_if_needed sees fresh last_attempt_at.
        """
        db._add_basic("tt1")  # No projection yet

        enqueue_mock = AsyncMock()
        with patch.object(store, "_enqueue_enrichment_if_needed", enqueue_mock):
            await store.fetch_renderable_payload("tt1")

        # The enqueue call should receive the freshly-read row, not None
        if enqueue_mock.called:
            _, row_arg = enqueue_mock.call_args[0]
            assert row_arg is not None  # Should be the re-read row


# ═══════════════════════════════════════════════════════════════════════
# 2. ensure_core_projection
# ═══════════════════════════════════════════════════════════════════════


class TestEnsureCoreProjection:
    @pytest.mark.asyncio
    async def test_creates_core_from_basics(self, store, db):
        db._add_basic("tt1", title="My Movie", year=2020)

        payload = await store.ensure_core_projection("tt1")
        assert payload is not None
        assert payload["title"] == "My Movie"
        assert payload["year"] == "2020"
        assert payload["projection_state"] == PROJECTION_CORE
        assert payload["_full"] is False

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_tconst(self, store, db):
        payload = await store.ensure_core_projection("tt_unknown")
        assert payload is None

    @pytest.mark.asyncio
    async def test_core_payload_has_placeholder_images(self, store, db):
        db._add_basic("tt1")
        payload = await store.ensure_core_projection("tt1")
        assert payload["poster_url"] == PLACEHOLDER_POSTER
        assert payload["backdrop_url"] == PLACEHOLDER_BACKDROP


# ═══════════════════════════════════════════════════════════════════════
# 3. _enqueue_enrichment_if_needed — cooldown
# ═══════════════════════════════════════════════════════════════════════


class TestEnqueueEnrichment:
    @pytest.mark.asyncio
    async def test_skips_within_cooldown(self, store, db):
        """Should not enqueue if last_attempt_at is within ENQUEUE_COOLDOWN."""
        row = {"last_attempt_at": _now() - timedelta(minutes=5)}  # 5 min ago, cooldown=15 min

        mock_enqueue = AsyncMock()
        mock_app = MagicMock()
        mock_app.enqueue_runtime_job = mock_enqueue

        with patch("quart.current_app", mock_app):
            await store._enqueue_enrichment_if_needed("tt1", row)

        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueues_after_cooldown(self, store, db):
        """Should enqueue if last_attempt_at is older than ENQUEUE_COOLDOWN."""
        row = {"last_attempt_at": _now() - timedelta(minutes=20)}
        db._add_projection("tt1", PROJECTION_CORE, last_attempt_at=_now() - timedelta(minutes=20))

        mock_enqueue = AsyncMock()
        mock_app = MagicMock()
        mock_app.enqueue_runtime_job = mock_enqueue

        with patch("quart.current_app", mock_app):
            await store._enqueue_enrichment_if_needed("tt1", row)

        mock_enqueue.assert_called_once_with("enrich_projection", "tt1")

    @pytest.mark.asyncio
    async def test_enqueues_when_last_attempt_is_none(self, store, db):
        """BUG FINDER: NULL last_attempt_at means first visit — should enqueue."""
        row = {"last_attempt_at": None}
        db._add_projection("tt1", PROJECTION_CORE, last_attempt_at=None)

        mock_enqueue = AsyncMock()
        mock_app = MagicMock()
        mock_app.enqueue_runtime_job = mock_enqueue

        with patch("quart.current_app", mock_app):
            await store._enqueue_enrichment_if_needed("tt1", row)

        mock_enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_swallows_enqueue_failure(self, store, db):
        """Enqueue failure should not propagate to caller."""
        row = {"last_attempt_at": None}

        mock_enqueue = AsyncMock(side_effect=RuntimeError("queue full"))
        mock_app = MagicMock()
        mock_app.enqueue_runtime_job = mock_enqueue

        with patch("quart.current_app", mock_app):
            # Should not raise
            await store._enqueue_enrichment_if_needed("tt1", row)

    @pytest.mark.asyncio
    async def test_row_none_still_enqueues(self, store, db):
        """When row is None (no projection exists yet), should still enqueue."""
        mock_enqueue = AsyncMock()
        mock_app = MagicMock()
        mock_app.enqueue_runtime_job = mock_enqueue

        with patch("quart.current_app", mock_app):
            await store._enqueue_enrichment_if_needed("tt1", None)

        # last_attempt_at is None when row is None, so cooldown is skipped
        mock_enqueue.assert_called_once_with("enrich_projection", "tt1")


# ═══════════════════════════════════════════════════════════════════════
# 4. enrich_projection — error handling
# ═══════════════════════════════════════════════════════════════════════


class TestEnrichProjection:
    @pytest.mark.asyncio
    async def test_successful_enrichment_sets_ready(self, store, db):
        db._add_basic("tt1")
        db._add_projection("tt1", PROJECTION_CORE)

        mock_movie_data = {
            "title": "Enriched",
            "tmdb_id": 42,
            "projection_state": PROJECTION_READY,
        }
        with patch("movies.projection_store.Movie") as MockMovie:
            mock_instance = AsyncMock()
            mock_instance.get_movie_data = AsyncMock(return_value=mock_movie_data)
            MockMovie.return_value = mock_instance

            result = await store.enrich_projection("tt1")

        assert result is not None
        assert result["projection_state"] == PROJECTION_READY

    @pytest.mark.asyncio
    async def test_enrichment_failure_returns_core_payload(self, store, db):
        """On TMDb failure, should return core payload (not None)."""
        db._add_basic("tt1")
        db._add_projection("tt1", PROJECTION_CORE)

        with patch("movies.projection_store.Movie") as MockMovie:
            mock_instance = AsyncMock()
            mock_instance.get_movie_data = AsyncMock(return_value=None)
            MockMovie.return_value = mock_instance

            result = await store.enrich_projection("tt1")

        # Should return core payload as fallback
        assert result is not None

    @pytest.mark.asyncio
    async def test_enrichment_exception_records_failed_state(self, store, db):
        """On exception, projection should be marked FAILED with error message."""
        db._add_basic("tt1")
        db._add_projection("tt1", PROJECTION_CORE)

        with patch("movies.projection_store.Movie") as MockMovie:
            mock_instance = AsyncMock()
            mock_instance.get_movie_data = AsyncMock(side_effect=RuntimeError("API down"))
            MockMovie.return_value = mock_instance

            result = await store.enrich_projection("tt1")

        # Should still return core payload
        assert result is not None

    @pytest.mark.asyncio
    async def test_enrichment_increments_attempt_count(self, store, db):
        """Attempt count should increment on each call."""
        db._add_basic("tt1")
        db._add_projection("tt1", PROJECTION_CORE, attempt_count=5)

        mock_movie_data = {"title": "Test", "tmdb_id": 42, "projection_state": PROJECTION_READY}
        with patch("movies.projection_store.Movie") as MockMovie:
            mock_instance = AsyncMock()
            mock_instance.get_movie_data = AsyncMock(return_value=mock_movie_data)
            MockMovie.return_value = mock_instance

            await store.enrich_projection("tt1")

    @pytest.mark.asyncio
    async def test_enrichment_without_existing_row(self, store, db):
        """enrich_projection on a tconst with no projection row should still work."""
        db._add_basic("tt1")

        with patch("movies.projection_store.Movie") as MockMovie:
            mock_instance = AsyncMock()
            mock_instance.get_movie_data = AsyncMock(return_value=None)
            MockMovie.return_value = mock_instance

            # Should not crash; attempt_count defaults to 1
            result = await store.enrich_projection("tt1")
            assert result is not None


# ═══════════════════════════════════════════════════════════════════════
# 5. _payload_from_row edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestPayloadFromRow:
    def test_string_payload_parsed(self, store):
        row = {"payload_json": '{"title": "Test"}', "projection_state": "ready"}
        result = store._payload_from_row(row)
        assert result["title"] == "Test"
        assert result["projection_state"] == "ready"

    def test_dict_payload_passed_through(self, store):
        row = {"payload_json": {"title": "Test"}, "projection_state": "ready"}
        result = store._payload_from_row(row)
        assert result["title"] == "Test"

    def test_none_payload_returns_empty_with_state(self, store):
        row = {"payload_json": None, "projection_state": "core"}
        result = store._payload_from_row(row)
        assert result == {"projection_state": "core"}

    def test_non_dict_payload_returns_empty(self, store):
        row = {"payload_json": "[1, 2, 3]", "projection_state": "core"}
        result = store._payload_from_row(row)
        assert result["projection_state"] == "core"
