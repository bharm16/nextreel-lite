"""Tests for movies/projection_store.py — ProjectionStore class."""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from movies.projection_store import (
    PLACEHOLDER_BACKDROP,
    PLACEHOLDER_POSTER,
    PROJECTION_CORE,
    PROJECTION_FAILED,
    PROJECTION_READY,
    PROJECTION_STALE,
    STALE_AFTER,
    ProjectionStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(db_pool, tmdb_helper=None):
    return ProjectionStore(db_pool, tmdb_helper=tmdb_helper)


def _core_db_row(**overrides):
    """Minimal row returned by the title.basics + title.ratings query."""
    base = {
        "tconst": "tt1234567",
        "primaryTitle": "Test Movie",
        "startYear": 2020,
        "genres": "Drama,Comedy",
        "language": "en",
        "slug": "test-movie",
        "averageRating": 7.5,
        "numVotes": 10000,
    }
    base.update(overrides)
    return base


def _projection_row(**overrides):
    """Minimal row returned by _select_row."""
    base = {
        "tconst": "tt1234567",
        "tmdb_id": 42,
        "payload_json": json.dumps({"title": "Test Movie", "_full": True}),
        "projection_state": PROJECTION_READY,
        "enriched_at": datetime(2025, 1, 1),
        "stale_after": datetime(2099, 1, 1),
        "last_attempt_at": datetime(2025, 1, 1),
        "attempt_count": 1,
        "last_error": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants are defined correctly."""

    def test_projection_ready(self):
        assert PROJECTION_READY == "ready"

    def test_projection_core(self):
        assert PROJECTION_CORE == "core"

    def test_projection_failed(self):
        assert PROJECTION_FAILED == "failed"

    def test_projection_stale(self):
        assert PROJECTION_STALE == "stale"

    def test_stale_after_is_seven_days(self):
        assert STALE_AFTER == timedelta(days=7)


# ---------------------------------------------------------------------------
# _payload_from_row
# ---------------------------------------------------------------------------


class TestPayloadFromRow:
    """ProjectionStore._payload_from_row() parsing and fallback logic."""

    def test_parses_json_string(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        row = {
            "payload_json": '{"title": "Foo", "year": "2020"}',
            "projection_state": PROJECTION_READY,
        }
        result = store._payload_from_row(row)
        assert result["title"] == "Foo"
        assert result["year"] == "2020"
        assert result["projection_state"] == PROJECTION_READY

    def test_handles_dict_directly(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        row = {
            "payload_json": {"title": "Bar"},
            "projection_state": PROJECTION_CORE,
        }
        result = store._payload_from_row(row)
        assert result["title"] == "Bar"
        assert result["projection_state"] == PROJECTION_CORE

    def test_none_payload_returns_empty_dict_with_state(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        row = {"payload_json": None, "projection_state": PROJECTION_FAILED}
        result = store._payload_from_row(row)
        assert result == {"projection_state": PROJECTION_FAILED}

    def test_invalid_json_returns_empty_dict(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        row = {"payload_json": "not valid json{{{", "projection_state": PROJECTION_CORE}
        with pytest.raises(json.JSONDecodeError):
            store._payload_from_row(row)

    def test_non_dict_payload_returns_empty_dict(self, mock_db_pool):
        """payload_json that is a list or number yields empty dict."""
        store = _make_store(mock_db_pool)
        row = {"payload_json": "[1, 2, 3]", "projection_state": PROJECTION_READY}
        result = store._payload_from_row(row)
        assert result == {"projection_state": PROJECTION_READY}

    def test_existing_projection_state_not_overwritten(self, mock_db_pool):
        """setdefault should not overwrite a projection_state already in payload."""
        store = _make_store(mock_db_pool)
        row = {
            "payload_json": '{"projection_state": "ready", "title": "X"}',
            "projection_state": PROJECTION_CORE,
        }
        result = store._payload_from_row(row)
        assert result["projection_state"] == PROJECTION_READY


# ---------------------------------------------------------------------------
# build_core_payload
# ---------------------------------------------------------------------------


class TestBuildCorePayload:
    """ProjectionStore.build_core_payload() output shape and defaults."""

    def test_all_expected_fields_present(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        row = _core_db_row()
        payload = store.build_core_payload(row)

        expected_keys = {
            "title",
            "imdb_id",
            "tmdb_id",
            "slug",
            "genres",
            "directors",
            "rating",
            "votes",
            "plot",
            "poster_url",
            "year",
            "cast",
            "trailer",
            "backdrop_url",
            "original_language",
            "spoken_languages",
            "age_rating",
            "budget",
            "revenue",
            "runtime",
            "production_countries",
            "status",
            "tagline",
            "watch_providers",
            "key_crew",
            "keywords",
            "recommendations",
            "external_ids",
            "collection",
            "homepage",
            "_full",
            "projection_state",
        }
        assert set(payload.keys()) == expected_keys

    def test_placeholder_urls(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row())
        assert payload["poster_url"] == PLACEHOLDER_POSTER
        assert payload["backdrop_url"] == PLACEHOLDER_BACKDROP

    def test_full_is_false(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row())
        assert payload["_full"] is False

    def test_projection_state_is_core(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row())
        assert payload["projection_state"] == PROJECTION_CORE

    def test_values_from_row(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        row = _core_db_row(primaryTitle="Blade Runner", startYear=1982, genres="Sci-Fi")
        payload = store.build_core_payload(row)
        assert payload["title"] == "Blade Runner"
        assert payload["year"] == "1982"
        assert payload["genres"] == "Sci-Fi"
        assert payload["imdb_id"] == "tt1234567"

    def test_rating_is_float_votes_is_int(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row(averageRating=8.1, numVotes=5000))
        assert isinstance(payload["rating"], float)
        assert payload["rating"] == 8.1
        assert isinstance(payload["votes"], int)
        assert payload["votes"] == 5000

    def test_missing_language_defaults(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row(language=None))
        assert payload["original_language"] == "unknown"
        assert payload["spoken_languages"] == []

    def test_known_language_in_spoken_languages(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row(language="fr"))
        assert payload["spoken_languages"] == ["fr"]

    def test_missing_title_defaults_to_unknown(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row(primaryTitle=None))
        assert payload["title"] == "Unknown"

    def test_missing_year_defaults_to_unknown(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row(startYear=None))
        assert payload["year"] == "Unknown"

    def test_zero_rating_and_votes(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        payload = store.build_core_payload(_core_db_row(averageRating=0, numVotes=0))
        assert payload["rating"] == 0.0
        assert payload["votes"] == 0


# ---------------------------------------------------------------------------
# ensure_core_projection
# ---------------------------------------------------------------------------


class TestEnsureCoreProjection:
    """ProjectionStore.ensure_core_projection() DB interactions."""

    async def test_returns_payload_when_db_has_data(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _core_db_row(),  # SELECT from title.basics
                None,  # INSERT ... ON DUPLICATE KEY
            ]
        )
        store = _make_store(mock_db_pool)
        result = await store.ensure_core_projection("tt1234567")

        assert result is not None
        assert result["imdb_id"] == "tt1234567"
        assert result["projection_state"] == PROJECTION_CORE
        assert result["_full"] is False

    async def test_returns_none_when_not_found(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(return_value=None)
        store = _make_store(mock_db_pool)
        result = await store.ensure_core_projection("tt0000000")
        assert result is None

    async def test_executes_insert_on_duplicate_key(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _core_db_row(),
                None,
            ]
        )
        store = _make_store(mock_db_pool)
        await store.ensure_core_projection("tt1234567")

        assert mock_db_pool.execute.call_count == 2
        insert_call = mock_db_pool.execute.call_args_list[1]
        sql = insert_call[0][0]
        assert "INSERT INTO movie_projection" in sql
        assert "ON DUPLICATE KEY UPDATE" in sql

    async def test_insert_params_contain_tconst_and_core_state(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _core_db_row(tconst="tt9999999"),
                None,
            ]
        )
        store = _make_store(mock_db_pool)
        await store.ensure_core_projection("tt9999999")

        insert_call = mock_db_pool.execute.call_args_list[1]
        params = insert_call[0][1]
        assert params[0] == "tt9999999"
        assert params[3] == PROJECTION_CORE

    async def test_insert_payload_is_valid_json(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _core_db_row(),
                None,
            ]
        )
        store = _make_store(mock_db_pool)
        await store.ensure_core_projection("tt1234567")

        insert_call = mock_db_pool.execute.call_args_list[1]
        params = insert_call[0][1]
        payload_json = params[2]
        parsed = json.loads(payload_json)
        assert parsed["title"] == "Test Movie"
        assert parsed["projection_state"] == PROJECTION_CORE


# ---------------------------------------------------------------------------
# enrich_projection — success
# ---------------------------------------------------------------------------


async def test_enrich_projection_wrapper_delegates_to_coordinator(mock_db_pool):
    store = _make_store(mock_db_pool)
    store.coordinator.enrich_projection = AsyncMock(return_value={"title": "Delegated"})

    result = await store.enrich_projection("tt1234567", known_tmdb_id=42)

    store.coordinator.enrich_projection.assert_awaited_once_with(
        "tt1234567",
        known_tmdb_id=42,
    )
    assert result == {"title": "Delegated"}


class TestEnrichProjectionSuccess:
    """enrich_projection() happy path — Movie returns data."""

    async def test_calls_movie_get_movie_data(self, mock_db_pool):
        enriched = {"title": "Enriched", "tmdb_id": 99, "_full": True}
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(),  # _select_row
                None,  # INSERT for ready state
            ]
        )
        with patch("movies.projection_enrichment.Movie") as MockMovie:
            mock_movie = MockMovie.return_value
            mock_movie.get_movie_data = AsyncMock(return_value=enriched)

            store = _make_store(mock_db_pool, tmdb_helper=MagicMock())
            result = await store.enrich_projection("tt1234567")

        MockMovie.assert_called_once_with("tt1234567", mock_db_pool, tmdb_helper=store.tmdb_helper)
        mock_movie.get_movie_data.assert_awaited_once_with(known_tmdb_id=42)
        assert result["projection_state"] == PROJECTION_READY

    async def test_saves_with_ready_state(self, mock_db_pool):
        enriched = {"title": "Enriched", "tmdb_id": 99}
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(),
                None,
            ]
        )
        with patch("movies.projection_enrichment.Movie") as MockMovie:
            MockMovie.return_value.get_movie_data = AsyncMock(return_value=enriched)
            store = _make_store(mock_db_pool)
            await store.enrich_projection("tt1234567")

        insert_call = mock_db_pool.execute.call_args_list[1]
        params = insert_call[0][1]
        # params[3] is projection_state
        assert params[3] == PROJECTION_READY

    async def test_stale_after_is_now_plus_seven_days(self, mock_db_pool):
        enriched = {"title": "X", "tmdb_id": 1}
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(),
                None,
            ]
        )
        with patch("movies.projection_enrichment.Movie") as MockMovie:
            MockMovie.return_value.get_movie_data = AsyncMock(return_value=enriched)
            store = _make_store(mock_db_pool)
            await store.enrich_projection("tt1234567")

        insert_call = mock_db_pool.execute.call_args_list[1]
        params = insert_call[0][1]
        enriched_at = params[4]
        stale_after = params[5]
        assert stale_after - enriched_at == STALE_AFTER

    async def test_increments_attempt_count(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(attempt_count=3),
                None,
            ]
        )
        with patch("movies.projection_enrichment.Movie") as MockMovie:
            MockMovie.return_value.get_movie_data = AsyncMock(return_value={"title": "X"})
            store = _make_store(mock_db_pool)
            await store.enrich_projection("tt1234567")

        insert_call = mock_db_pool.execute.call_args_list[1]
        params = insert_call[0][1]
        # params[7] is attempt_count
        assert params[7] == 4

    async def test_first_attempt_when_no_existing_row(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                None,  # _select_row returns None
                None,  # INSERT
            ]
        )
        with patch("movies.projection_enrichment.Movie") as MockMovie:
            MockMovie.return_value.get_movie_data = AsyncMock(return_value={"title": "X"})
            store = _make_store(mock_db_pool)
            await store.enrich_projection("tt1234567")

        insert_call = mock_db_pool.execute.call_args_list[1]
        params = insert_call[0][1]
        assert params[7] == 1  # attempt_count


# ---------------------------------------------------------------------------
# enrich_projection — failure
# ---------------------------------------------------------------------------


class TestEnrichProjectionFailure:
    """enrich_projection() error path — falls back to core projection."""

    async def test_falls_back_to_core_on_exception(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(),  # _select_row
                _core_db_row(),  # ensure_core_projection SELECT
                None,  # ensure_core_projection INSERT
                None,  # failure INSERT
            ]
        )
        with patch("movies.projection_enrichment.Movie") as MockMovie:
            MockMovie.return_value.get_movie_data = AsyncMock(side_effect=RuntimeError("TMDb down"))
            store = _make_store(mock_db_pool)
            result = await store.enrich_projection("tt1234567")

        assert result is not None
        assert result["projection_state"] == PROJECTION_CORE

    async def test_saves_failed_state_with_error_message(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(),
                _core_db_row(),
                None,
                None,
            ]
        )
        with patch("movies.projection_enrichment.Movie") as MockMovie:
            MockMovie.return_value.get_movie_data = AsyncMock(side_effect=ValueError("bad data"))
            store = _make_store(mock_db_pool)
            await store.enrich_projection("tt1234567")

        # The last execute call is the failure INSERT
        failure_call = mock_db_pool.execute.call_args_list[-1]
        params = failure_call[0][1]
        assert params[3] == PROJECTION_FAILED
        assert "bad data" in params[6]  # last_error

    async def test_null_payload_raises_and_falls_back(self, mock_db_pool):
        """get_movie_data returning None triggers RuntimeError internally."""
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(),
                _core_db_row(),
                None,
                None,
            ]
        )
        with patch("movies.projection_enrichment.Movie") as MockMovie:
            MockMovie.return_value.get_movie_data = AsyncMock(return_value=None)
            store = _make_store(mock_db_pool)
            result = await store.enrich_projection("tt1234567")

        assert result is not None
        failure_call = mock_db_pool.execute.call_args_list[-1]
        params = failure_call[0][1]
        assert params[3] == PROJECTION_FAILED
        assert "no payload" in params[6].lower()


# ---------------------------------------------------------------------------
# requeue_stale_projections
# ---------------------------------------------------------------------------


class TestRequeueStaleProjections:
    """ProjectionStore.requeue_stale_projections() row-count logic."""

    async def test_returns_count_of_updated_rows(self, mock_db_pool):
        # Both loops (ready->stale, failed->stale) run; each returns 5 then exits.
        mock_db_pool.execute = AsyncMock(return_value=5)
        store = _make_store(mock_db_pool)
        count = await store.requeue_stale_projections()
        assert count == 10

    async def test_returns_zero_when_result_is_not_int(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(return_value=None)
        store = _make_store(mock_db_pool)
        count = await store.requeue_stale_projections()
        assert count == 0

    async def test_query_targets_ready_state(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(return_value=0)
        store = _make_store(mock_db_pool)
        await store.requeue_stale_projections()

        # First call is the ready->stale loop.
        first_call = mock_db_pool.execute.call_args_list[0]
        params = first_call[0][1]
        assert params[0] == PROJECTION_STALE
        assert params[1] == PROJECTION_READY

    async def test_returns_zero_when_no_rows_updated(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(return_value=0)
        store = _make_store(mock_db_pool)
        count = await store.requeue_stale_projections()
        assert count == 0

    async def test_failed_recovery_sweep_runs_second_update(self, mock_db_pool):
        """Both ready->stale and failed->stale UPDATEs are issued."""
        from movies.projection_state import FAILED_RETRY_COOLDOWN

        # Each loop returns less-than-batch-size on first call so each exits
        # after one iteration. 3 + 7 = 10 total.
        mock_db_pool.execute = AsyncMock(side_effect=[3, 7])
        store = _make_store(mock_db_pool)
        count = await store.requeue_stale_projections()

        assert count == 10
        assert mock_db_pool.execute.await_count == 2

        first_call, second_call = mock_db_pool.execute.await_args_list
        first_params = first_call.args[1]
        second_params = second_call.args[1]

        # First UPDATE: ready -> stale
        assert first_params[0] == PROJECTION_STALE
        assert first_params[1] == PROJECTION_READY

        # Second UPDATE: failed -> stale, with cooldown threshold
        second_sql = second_call.args[0]
        assert "projection_state" in second_sql
        assert second_params[0] == PROJECTION_STALE
        assert second_params[1] == PROJECTION_FAILED
        # cutoff is "now - FAILED_RETRY_COOLDOWN"; verify it's roughly that
        from infra.time_utils import utcnow as _utcnow
        cutoff = second_params[2]
        delta = _utcnow() - cutoff
        # Should be within a few seconds of FAILED_RETRY_COOLDOWN
        assert abs(delta.total_seconds() - FAILED_RETRY_COOLDOWN.total_seconds()) < 5


async def test_ready_check_is_select_only(mock_db_pool):
    mock_db_pool.execute = AsyncMock(return_value={"ready": 1})
    store = _make_store(mock_db_pool)

    result = await store.ready_check()

    assert result is True
    mock_db_pool.execute.assert_awaited_once()
    query = mock_db_pool.execute.await_args.args[0]
    assert "SELECT 1 AS ready" in query


# ---------------------------------------------------------------------------
# fetch_renderable_payload
# ---------------------------------------------------------------------------


class TestFetchRenderablePayload:
    async def test_ready_payload_returns_without_enqueue(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(return_value=_projection_row())
        enqueue = AsyncMock()
        store = ProjectionStore(mock_db_pool, enqueue_fn=enqueue)

        payload = await store.fetch_renderable_payload("tt1234567")

        assert payload["title"] == "Test Movie"
        enqueue.assert_not_awaited()

    async def test_stale_payload_enqueues_and_returns_existing_payload(self, mock_db_pool):
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(projection_state=PROJECTION_STALE),
                None,
            ]
        )
        enqueue = AsyncMock(return_value=object())
        store = ProjectionStore(mock_db_pool, enqueue_fn=enqueue)

        payload = await store.fetch_renderable_payload("tt1234567")

        assert payload["projection_state"] == PROJECTION_STALE
        enqueue.assert_awaited_once_with(
            "enrich_projection", "tt1234567", 42, _job_id="enrich:tt1234567"
        )

    async def test_core_payload_enriches_inline_before_returning(self, mock_db_pool):
        enriched = {
            "title": "Enriched Movie",
            "tmdb_id": 42,
            "projection_state": "ready",
            "_full": True,
        }
        mock_db_pool.execute = AsyncMock(
            side_effect=[
                _projection_row(projection_state=PROJECTION_CORE),
                # enrich_projection calls: _select_row, _upsert_ready
                _projection_row(projection_state=PROJECTION_CORE),
                None,
            ]
        )
        store = ProjectionStore(mock_db_pool, tmdb_helper=MagicMock())
        store.coordinator.enrich_projection = AsyncMock(return_value=enriched)

        payload = await store.fetch_renderable_payload("tt1234567")

        assert payload["title"] == "Enriched Movie"
        assert payload["projection_state"] == "ready"
        store.coordinator.enrich_projection.assert_awaited_once_with("tt1234567", known_tmdb_id=42)

    async def test_cold_miss_enriches_inline_before_returning(self, mock_db_pool):
        enriched = {
            "title": "Enriched Movie",
            "tmdb_id": 99,
            "projection_state": "ready",
            "_full": True,
        }
        mock_db_pool.execute = AsyncMock(return_value=None)
        store = ProjectionStore(mock_db_pool, tmdb_helper=MagicMock())
        store.coordinator.enrich_projection = AsyncMock(return_value=enriched)

        payload = await store.fetch_renderable_payload("tt1234567")

        assert payload["title"] == "Enriched Movie"
        assert payload["_full"] is True
        store.coordinator.enrich_projection.assert_awaited_once_with(
            "tt1234567", known_tmdb_id=None
        )

    async def test_local_enrichment_is_deduped_per_tconst(self, mock_db_pool):
        store = ProjectionStore(mock_db_pool, tmdb_helper=MagicMock())
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_enrich_projection(tconst, known_tmdb_id=None):
            started.set()
            await release.wait()
            return {"tconst": tconst, "tmdb_id": known_tmdb_id}

        store.coordinator.enrich_projection = AsyncMock(side_effect=fake_enrich_projection)

        first = await store._schedule_local_enrichment("tt1234567", tmdb_id=42)
        second = await store._schedule_local_enrichment("tt1234567", tmdb_id=42)

        assert first is True
        assert second is False

        await started.wait()
        release.set()
        if store._local_enrichment_tasks:
            await asyncio.gather(*store._local_enrichment_tasks)

        store.coordinator.enrich_projection.assert_awaited_once_with(
            "tt1234567",
            known_tmdb_id=42,
        )

    async def test_coordinator_aclose_drains_local_enrichment_tasks(self, mock_db_pool):
        store = ProjectionStore(mock_db_pool, tmdb_helper=MagicMock())
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_enrich_projection(tconst, known_tmdb_id=None):
            started.set()
            await release.wait()
            return {"tconst": tconst, "tmdb_id": known_tmdb_id}

        store.coordinator.enrich_projection = AsyncMock(side_effect=fake_enrich_projection)

        scheduled = await store._schedule_local_enrichment("tt1234567", tmdb_id=7)
        assert scheduled is True

        await started.wait()
        release.set()
        await store.coordinator.aclose(timeout=1.0)

        assert not store._local_enrichment_tasks
        store.coordinator.enrich_projection.assert_awaited_once_with(
            "tt1234567",
            known_tmdb_id=7,
        )


async def test_requeue_stale_projections_batches_under_limit():
    mock_pool = AsyncMock()
    # ready->stale loop: full 500, then 150 (partial -> exits).
    # failed->stale loop: 0 (exits immediately).
    mock_pool.execute = AsyncMock(side_effect=[500, 150, 0])

    store = ProjectionStore(mock_pool)
    total = await store.requeue_stale_projections()

    assert total == 650
    assert mock_pool.execute.await_count == 3
    for call in mock_pool.execute.await_args_list:
        query = call.args[0]
        assert "LIMIT" in query.upper()


async def test_requeue_stale_projections_exits_immediately_when_empty():
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=0)

    store = ProjectionStore(mock_pool)
    total = await store.requeue_stale_projections()

    assert total == 0
    # Both loops fire one iteration each before exiting on 0.
    assert mock_pool.execute.await_count == 2


async def test_requeue_stale_projections_safety_cap_prevents_infinite_loop():
    """If DB never returns less than batch size, loop must exit after max_iterations."""
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=500)  # always full batch

    store = ProjectionStore(mock_pool)
    total = await store.requeue_stale_projections()

    # Safety cap on each loop: 100 iterations x 500 rows = 50000 each, x2 loops.
    assert mock_pool.execute.await_count == 200
    assert total == 100000


# ---------------------------------------------------------------------------
# fetch_renderable_payloads (batched)
# ---------------------------------------------------------------------------


class TestFetchRenderablePayloadsBatched:
    async def test_empty_input_returns_empty_dict(self, mock_db_pool):
        store = _make_store(mock_db_pool)
        result = await store.fetch_renderable_payloads([])
        assert result == {}
        mock_db_pool.execute.assert_not_called()

    async def test_returns_dict_keyed_by_tconst(self, mock_db_pool):
        mock_db_pool.execute.return_value = [
            {
                "tconst": "tt1",
                "tmdb_id": 1,
                "payload_json": json.dumps({"title": "One"}),
                "projection_state": PROJECTION_READY,
                "enriched_at": None,
                "stale_after": None,
                "last_attempt_at": None,
                "attempt_count": 0,
                "last_error": None,
            },
            {
                "tconst": "tt2",
                "tmdb_id": 2,
                "payload_json": json.dumps({"title": "Two"}),
                "projection_state": PROJECTION_READY,
                "enriched_at": None,
                "stale_after": None,
                "last_attempt_at": None,
                "attempt_count": 0,
                "last_error": None,
            },
        ]
        store = _make_store(mock_db_pool)
        result = await store.fetch_renderable_payloads(["tt1", "tt2"])
        assert set(result.keys()) == {"tt1", "tt2"}
        assert result["tt1"]["title"] == "One"
        assert result["tt2"]["title"] == "Two"

    async def test_missing_tconst_absent_from_dict(self, mock_db_pool):
        mock_db_pool.execute.return_value = [
            {
                "tconst": "tt1",
                "tmdb_id": None,
                "payload_json": json.dumps({"title": "One"}),
                "projection_state": PROJECTION_READY,
                "enriched_at": None,
                "stale_after": None,
                "last_attempt_at": None,
                "attempt_count": 0,
                "last_error": None,
            },
        ]
        store = _make_store(mock_db_pool)
        result = await store.fetch_renderable_payloads(["tt1", "tt_missing"])
        assert "tt1" in result
        assert "tt_missing" not in result
