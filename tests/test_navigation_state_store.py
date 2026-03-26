"""Tests for NavigationStateStore — persistence, optimistic locking, migration.

These tests target the most dangerous untested invariants in the state store:
  1. mutate() conflict exhaustion → state=None dereference
  2. save_state() timestamp mutation before DB confirmation
  3. _touch_if_needed() rowcount contract
  4. load_for_request() branching (expired, legacy, fresh)
  5. dual_write_enabled() time-based cutover
  6. _state_from_legacy() data normalization
  7. session ID validation edge cases
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infra.navigation_state import (
    MIGRATION_META_LAST_IMPORT_AT,
    MIGRATION_META_STARTED_AT,
    PREV_STACK_MAX,
    QUEUE_TARGET,
    SEEN_MAX,
    NavigationState,
    NavigationStateStore,
    _is_valid_session_id,
    default_filter_state,
    normalize_filters,
    utcnow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_state(session_id="aabbccdd" * 4, version=1, **overrides):
    now = _now()
    defaults = dict(
        session_id=session_id,
        version=version,
        csrf_token="tok",
        filters=default_filter_state(),
        current_tconst=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
    )
    defaults.update(overrides)
    return NavigationState(**defaults)


class FakeDBPool:
    """In-memory fake that simulates the subset of db_pool used by NavigationStateStore."""

    def __init__(self):
        self.rows: dict[str, dict] = {}  # session_id → row dict
        self.meta: dict[str, str] = {}   # meta_key → meta_value
        self.execute_calls: list[tuple] = []

    async def execute(self, query, params=None, fetch="one", **kw):
        params = params or []
        self.execute_calls.append((query.strip(), list(params), fetch))

        q = query.strip().upper()

        # --- runtime_metadata ---
        if "FROM RUNTIME_METADATA" in q:
            key = params[0]
            if key in self.meta:
                return {"meta_value": self.meta[key]}
            return None
        if "INSERT INTO RUNTIME_METADATA" in q:
            key, value = params[0], params[1]
            self.meta[key] = value
            return 1

        # --- SELECT user_navigation_state ---
        if q.startswith("SELECT") and "USER_NAVIGATION_STATE" in q:
            sid = params[0]
            return self.rows.get(sid)

        # --- INSERT user_navigation_state ---
        if q.startswith("INSERT INTO USER_NAVIGATION_STATE"):
            sid = params[0]
            self.rows[sid] = {
                "session_id": params[0],
                "version": params[1],
                "csrf_token": params[2],
                "filters_json": params[3],
                "current_tconst": params[4],
                "queue_json": params[5],
                "prev_json": params[6],
                "future_json": params[7],
                "seen_json": params[8],
                "created_at": params[9],
                "last_activity_at": params[10],
                "expires_at": params[11],
            }
            return 1

        # --- UPDATE user_navigation_state (save_state — with version check) ---
        # Must be checked BEFORE the touch handler because both contain LAST_ACTIVITY_AT.
        if q.startswith("UPDATE USER_NAVIGATION_STATE") and "ANDVERSION=%S" in q.replace(" ", ""):
            # Params: [next_version, csrf, filters, current, queue, prev, future, seen,
            #          last_activity_at, expires_at, session_id, expected_version]
            next_version = params[0]
            sid = params[10]
            expected_version = params[11]
            row = self.rows.get(sid)
            if not row or row["version"] != expected_version:
                return 0  # conflict
            row["version"] = next_version
            row["csrf_token"] = params[1]
            row["filters_json"] = params[2]
            row["current_tconst"] = params[3]
            row["queue_json"] = params[4]
            row["prev_json"] = params[5]
            row["future_json"] = params[6]
            row["seen_json"] = params[7]
            row["last_activity_at"] = params[8]
            row["expires_at"] = params[9]
            return 1

        # --- UPDATE user_navigation_state (touch — no version check) ---
        if q.startswith("UPDATE USER_NAVIGATION_STATE") and "LAST_ACTIVITY_AT" in q:
            sid = params[2]
            row = self.rows.get(sid)
            if not row:
                return 0
            row["last_activity_at"] = params[0]
            row["expires_at"] = params[1]
            return 1

        # --- DELETE ---
        if q.startswith("DELETE"):
            sid = params[0]
            self.rows.pop(sid, None)
            return 1

        return None


@pytest.fixture
def db_pool():
    return FakeDBPool()


@pytest.fixture
def store(db_pool):
    return NavigationStateStore(db_pool)


# ═══════════════════════════════════════════════════════════════════════
# 1. Session ID validation
# ═══════════════════════════════════════════════════════════════════════


class TestSessionIdValidation:
    def test_valid_hex32(self):
        assert _is_valid_session_id("a" * 32) is True

    def test_rejects_short(self):
        assert _is_valid_session_id("a" * 31) is False

    def test_rejects_long(self):
        assert _is_valid_session_id("a" * 33) is False

    def test_rejects_uppercase(self):
        assert _is_valid_session_id("A" * 32) is False

    def test_rejects_dashes(self):
        # UUID with dashes is NOT valid — we use .hex format
        assert _is_valid_session_id("12345678-1234-1234-1234-123456789abc") is False

    def test_rejects_empty(self):
        assert _is_valid_session_id("") is False

    def test_rejects_none(self):
        assert _is_valid_session_id(None) is False


# ═══════════════════════════════════════════════════════════════════════
# 2. mutate() — optimistic locking and conflict exhaustion
# ═══════════════════════════════════════════════════════════════════════


class TestMutate:
    @pytest.mark.asyncio
    async def test_successful_mutation_increments_version(self, store, db_pool):
        state = _make_state()
        await store._insert_state(state)

        result = await store.mutate(state.session_id, lambda s: setattr(s, "current_tconst", "tt1"))
        assert not result.conflicted
        assert result.state.version == 2
        assert result.state.current_tconst == "tt1"

    @pytest.mark.asyncio
    async def test_conflict_on_version_mismatch(self, store, db_pool):
        """Simulate external version bump between load and save."""
        state = _make_state()
        await store._insert_state(state)

        call_count = 0

        def mutator(s):
            nonlocal call_count
            call_count += 1
            # Externally bump the version PAST what save_state will try,
            # so that every save attempt sees a mismatch.
            # mutate() loads current (version N), clones, mutates, tries save with expected=N.
            # We bump to N+2 so save's WHERE version=N fails.
            current_v = db_pool.rows[state.session_id]["version"]
            db_pool.rows[state.session_id]["version"] = current_v + 2
            s.current_tconst = "tt1"

        result = await store.mutate(state.session_id, mutator)
        assert result.conflicted
        assert call_count == 3  # 3 retries (max_attempts)

    @pytest.mark.asyncio
    async def test_conflict_exhaustion_returns_state_not_none(self, store, db_pool):
        """BUG FINDER: After 3 failed retries, result.state must not be None.

        mutate() returns MutationResult(state=await self.get_state(...)).
        If the session is still valid, state should be non-None.
        """
        state = _make_state()
        await store._insert_state(state)

        def mutator(s):
            current_v = db_pool.rows[state.session_id]["version"]
            db_pool.rows[state.session_id]["version"] = current_v + 2
            s.current_tconst = "tt1"

        result = await store.mutate(state.session_id, mutator)
        assert result.conflicted
        # The critical assertion: state should be loadable
        assert result.state is not None

    @pytest.mark.asyncio
    async def test_conflict_exhaustion_with_expired_session_returns_none(self, store, db_pool):
        """BUG FINDER: If session expires during retries, state IS None.

        Callers that dereference result.state.session_id after conflict
        will crash with AttributeError.
        """
        state = _make_state(expires_at=_now() - timedelta(seconds=1))
        await store._insert_state(state)

        def mutator(s):
            db_pool.rows[state.session_id]["version"] = 9999

        result = await store.mutate(state.session_id, mutator)
        assert result.conflicted
        # get_state() returns None for expired sessions
        assert result.state is None

    @pytest.mark.asyncio
    async def test_mutate_with_deleted_session(self, store, db_pool):
        """mutate() on a non-existent session returns conflicted with state=None."""
        result = await store.mutate("a" * 32, lambda s: None)
        assert result.conflicted
        assert result.state is None

    @pytest.mark.asyncio
    async def test_async_mutator_supported(self, store, db_pool):
        state = _make_state()
        await store._insert_state(state)

        async def mutator(s):
            await asyncio.sleep(0)
            s.current_tconst = "tt99"

        result = await store.mutate(state.session_id, mutator)
        assert not result.conflicted
        assert result.state.current_tconst == "tt99"


# ═══════════════════════════════════════════════════════════════════════
# 3. save_state() — timestamp mutation before confirmation
# ═══════════════════════════════════════════════════════════════════════


class TestSaveState:
    @pytest.mark.asyncio
    async def test_successful_save(self, store, db_pool):
        state = _make_state()
        await store._insert_state(state)

        state.current_tconst = "tt1"
        saved = await store.save_state(state, expected_version=1)
        assert saved is True
        assert state.version == 2

    @pytest.mark.asyncio
    async def test_conflict_returns_false(self, store, db_pool):
        state = _make_state()
        await store._insert_state(state)

        saved = await store.save_state(state, expected_version=999)
        assert saved is False
        # Version should NOT have been incremented
        assert state.version == 1

    @pytest.mark.asyncio
    async def test_timestamps_mutated_before_save(self, store, db_pool):
        """BUG FINDER: save_state mutates last_activity_at and expires_at
        before the UPDATE query. On conflict, the in-memory state has
        stale timestamps but original version — inconsistent.
        """
        state = _make_state()
        original_activity = state.last_activity_at
        await store._insert_state(state)

        # Force a conflict
        saved = await store.save_state(state, expected_version=999)
        assert saved is False
        # The timestamps WERE mutated even though save failed:
        assert state.last_activity_at != original_activity


# ═══════════════════════════════════════════════════════════════════════
# 4. _touch_if_needed — rowcount and throttling
# ═══════════════════════════════════════════════════════════════════════


class TestTouchIfNeeded:
    @pytest.mark.asyncio
    async def test_skips_within_one_minute(self, store, db_pool):
        state = _make_state(last_activity_at=_now())
        await store._insert_state(state)

        original_activity = state.last_activity_at
        touched = await store._touch_if_needed(state)
        assert touched.last_activity_at == original_activity
        # No UPDATE should have been issued
        update_calls = [c for c in db_pool.execute_calls if "UPDATE" in c[0].upper()]
        assert len(update_calls) == 0

    @pytest.mark.asyncio
    async def test_updates_after_one_minute(self, store, db_pool):
        old_time = _now() - timedelta(minutes=2)
        state = _make_state(last_activity_at=old_time)
        await store._insert_state(state)

        touched = await store._touch_if_needed(state)
        assert touched.last_activity_at > old_time

    @pytest.mark.asyncio
    async def test_deleted_session_logs_warning(self, store, db_pool):
        """BUG FINDER: If the session was deleted, UPDATE returns 0 rows.
        The new check should log a warning.
        """
        old_time = _now() - timedelta(minutes=2)
        state = _make_state(last_activity_at=old_time)
        # Don't insert — simulate deleted session
        # The UPDATE will return 0

        with patch("infra.navigation_state.logger") as mock_logger:
            touched = await store._touch_if_needed(state)
            mock_logger.warning.assert_called_once()
            assert "Touch failed" in mock_logger.warning.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════════
# 5. load_for_request — branching
# ═══════════════════════════════════════════════════════════════════════


class TestLoadForRequest:
    @pytest.mark.asyncio
    async def test_valid_cookie_loads_existing_state(self, store, db_pool):
        state = _make_state()
        await store._insert_state(state)

        loaded, needs_cookie = await store.load_for_request(state.session_id)
        assert loaded.session_id == state.session_id
        assert needs_cookie is False

    @pytest.mark.asyncio
    async def test_expired_cookie_creates_fresh_state(self, store, db_pool):
        state = _make_state(expires_at=_now() - timedelta(hours=1))
        await store._insert_state(state)

        loaded, needs_cookie = await store.load_for_request(state.session_id)
        assert loaded.session_id != state.session_id  # new session
        assert needs_cookie is True

    @pytest.mark.asyncio
    async def test_invalid_cookie_format_creates_fresh(self, store, db_pool):
        loaded, needs_cookie = await store.load_for_request("not-a-hex-uuid")
        assert needs_cookie is True
        assert len(loaded.session_id) == 32

    @pytest.mark.asyncio
    async def test_none_cookie_creates_fresh(self, store, db_pool):
        loaded, needs_cookie = await store.load_for_request(None)
        assert needs_cookie is True

    @pytest.mark.asyncio
    async def test_legacy_session_import(self, store, db_pool):
        """When MySQL state is missing but legacy session has data, import it."""
        # Ensure dual-write is enabled
        db_pool.meta[MIGRATION_META_STARTED_AT] = _now().isoformat()

        legacy = {
            "current_movie": {"imdb_id": "tt555", "title": "Legacy"},
            "watch_queue": [],
            "previous_stack": [],
            "future_stack": [],
            "criteria": {},
        }

        loaded, needs_cookie = await store.load_for_request(None, legacy_session=legacy)
        assert needs_cookie is True
        assert loaded.current_tconst == "tt555"

    @pytest.mark.asyncio
    async def test_no_legacy_data_creates_fresh(self, store, db_pool):
        """Empty legacy session should NOT trigger import."""
        db_pool.meta[MIGRATION_META_STARTED_AT] = _now().isoformat()
        legacy = {}

        loaded, needs_cookie = await store.load_for_request(None, legacy_session=legacy)
        assert needs_cookie is True
        assert loaded.current_tconst is None


# ═══════════════════════════════════════════════════════════════════════
# 6. NavigationState.clone() — deep copy
# ═══════════════════════════════════════════════════════════════════════


class TestClone:
    def test_clone_is_independent(self):
        state = _make_state(
            queue=[{"tconst": "tt1", "title": "One", "slug": "one"}],
            filters={"language": "en", "genres_selected": ["Drama"]},
        )
        cloned = state.clone()
        cloned.queue.append({"tconst": "tt2"})
        cloned.filters["language"] = "fr"
        # Original should be unaffected
        assert len(state.queue) == 1
        assert state.filters["language"] == "en"

    def test_clone_preserves_all_fields(self):
        state = _make_state(
            current_tconst="tt1",
            seen=["tt1", "tt2"],
        )
        cloned = state.clone()
        assert cloned.session_id == state.session_id
        assert cloned.version == state.version
        assert cloned.csrf_token == state.csrf_token
        assert cloned.current_tconst == state.current_tconst
        assert cloned.seen == state.seen
        assert cloned.created_at == state.created_at


# ═══════════════════════════════════════════════════════════════════════
# 7. normalize_filters — edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeFilters:
    def test_invalid_year_falls_back_to_default(self):
        form = MagicMock()
        form.get = lambda key, default=None: {"year_min": "not_a_number"}.get(key, default)
        form.getlist = lambda key: []

        result = normalize_filters(form)
        assert result["year_min"] == default_filter_state()["year_min"]

    def test_year_min_clamped_to_1800(self):
        form = MagicMock()
        form.get = lambda key, default=None: {"year_min": "100"}.get(key, default)
        form.getlist = lambda key: []

        result = normalize_filters(form)
        assert result["year_min"] == 1800

    def test_year_max_cannot_be_less_than_year_min(self):
        form = MagicMock()
        form.get = lambda key, default=None: {"year_min": "2000", "year_max": "1990"}.get(key, default)
        form.getlist = lambda key: []

        result = normalize_filters(form)
        assert result["year_max"] >= result["year_min"]

    def test_score_max_cannot_be_less_than_score_min(self):
        form = MagicMock()
        form.get = lambda key, default=None: {"imdb_score_min": "8.0", "imdb_score_max": "5.0"}.get(key, default)
        form.getlist = lambda key: []

        result = normalize_filters(form)
        assert result["imdb_score_max"] >= result["imdb_score_min"]

    def test_invalid_genre_rejected(self):
        form = MagicMock()
        form.get = lambda key, default=None: default
        form.getlist = lambda key: ["Drama", "NotARealGenre", "Action"]

        result = normalize_filters(form)
        assert "NotARealGenre" not in result["genres_selected"]
        assert "Drama" in result["genres_selected"]
        assert "Action" in result["genres_selected"]
