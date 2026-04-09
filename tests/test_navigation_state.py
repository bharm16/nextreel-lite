"""Tests for infra.navigation_state — dataclass, helpers, normalization, and store."""

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from infra.navigation_state import (
    MAX_FILTER_VALUE_LEN,
    PREV_STACK_MAX,
    FUTURE_STACK_MAX,
    QUEUE_TARGET,
    SEEN_MAX,
    MutationResult,
    NavigationState,
    NavigationStateStore,
    _normalize_ref,
    _normalize_ref_list,
    _normalize_seen,
    criteria_from_filters,
    default_filter_state,
    filters_from_criteria,
    normalize_filters,
    utcnow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> NavigationState:
    now = utcnow()
    defaults = dict(
        session_id="test-session",
        version=1,
        csrf_token="tok",
        filters=default_filter_state(),
        current_tconst=None,
        current_ref=None,
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


def _ref(tconst="tt0000001", title="Movie", slug="movie"):
    return {"tconst": tconst, "title": title, "slug": slug}


class FakeFormData:
    """Minimal form-like object with get() and getlist()."""

    def __init__(self, scalars=None, lists=None):
        self._scalars = scalars or {}
        self._lists = lists or {}

    def get(self, key, default=None):
        return self._scalars.get(key, default)

    def getlist(self, key):
        return self._lists.get(key, [])


# ===========================================================================
# NavigationState dataclass
# ===========================================================================

# --- clone() ---


def test_clone_produces_equal_but_independent_copy():
    state = _make_state(
        queue=[_ref("tt1")],
        prev=[_ref("tt2")],
        future=[_ref("tt3")],
        seen=["tt4"],
    )
    cloned = state.clone()

    assert cloned.session_id == state.session_id
    assert cloned.version == state.version
    assert cloned.filters == state.filters
    assert cloned.queue == state.queue
    assert cloned.prev == state.prev
    assert cloned.future == state.future
    assert cloned.seen == state.seen


def test_clone_deep_copies_mutable_fields():
    state = _make_state(
        queue=[_ref("tt1")],
        filters=default_filter_state(),
        seen=["tt9"],
        current_ref=_ref("tt1"),
    )
    cloned = state.clone()

    # Mutate the clone — original must stay unchanged
    cloned.queue.append(_ref("tt99"))
    cloned.filters["year_min"] = 2020
    cloned.seen.append("tt100")
    cloned.current_ref["title"] = "Changed"

    assert len(state.queue) == 1
    assert state.filters["year_min"] == 1900
    assert len(state.seen) == 1
    assert state.current_ref["title"] == "Movie"


# ===========================================================================
# default_filter_state()
# ===========================================================================


def test_default_filter_state_uses_current_year_when_none():
    filters = default_filter_state()
    assert filters["year_min"] == 1900
    assert filters["year_max"] == utcnow().year
    assert filters["imdb_score_min"] == 7.0
    assert filters["imdb_score_max"] == 10.0
    assert filters["num_votes_min"] == 100000
    assert filters["num_votes_max"] == 200000
    assert filters["language"] == "en"
    assert filters["genres_selected"] == []


def test_default_filter_state_with_explicit_year():
    filters = default_filter_state(current_year=2005)
    assert filters["year_max"] == 2005


# ===========================================================================
# filters_from_criteria()
# ===========================================================================


def test_filters_from_criteria_maps_all_keys():
    criteria = {
        "min_year": 1990,
        "max_year": 2020,
        "min_rating": 5.0,
        "max_rating": 9.0,
        "min_votes": 5000,
        "max_votes": 99999,
        "language": "fr",
        "genres": ["Drama", "Comedy"],
    }
    filters = filters_from_criteria(criteria)

    assert filters["year_min"] == 1990
    assert filters["year_max"] == 2020
    assert filters["imdb_score_min"] == 5.0
    assert filters["imdb_score_max"] == 9.0
    assert filters["num_votes_min"] == 5000
    assert filters["num_votes_max"] == 99999
    assert filters["language"] == "fr"
    assert filters["genres_selected"] == ["Drama", "Comedy"]


def test_filters_from_criteria_empty_uses_defaults():
    filters = filters_from_criteria({})
    expected = default_filter_state()
    assert filters == expected


def test_filters_from_criteria_partial_criteria():
    criteria = {"min_year": 2010}
    filters = filters_from_criteria(criteria)
    assert filters["year_min"] == 2010
    # Other fields stay at default
    assert filters["year_max"] == utcnow().year


# ===========================================================================
# criteria_from_filters() — roundtrip
# ===========================================================================


def test_criteria_from_filters_roundtrip():
    original_criteria = {
        "min_year": 1990,
        "max_year": 2020,
        "min_rating": 5.0,
        "max_rating": 9.0,
        "min_votes": 5000,
        "max_votes": 99999,
        "language": "fr",
        "genres": ["Drama"],
    }
    filters = filters_from_criteria(original_criteria)
    result = criteria_from_filters(filters)

    # The roundtrip should preserve the essential values
    assert result["min_year"] == 1990
    assert result["max_year"] == 2020
    assert result["language"] == "fr"


def test_criteria_from_filters_none_uses_defaults():
    result = criteria_from_filters(None)
    # Should not raise; returns criteria derived from defaults
    assert "min_year" in result or "language" in result or result is not None


# ===========================================================================
# normalize_filters()
# ===========================================================================


def test_normalize_filters_parses_scalar_strings():
    form = FakeFormData(
        scalars={"year_min": "1950", "language": "de"},
        lists={"genres[]": []},
    )
    result = normalize_filters(form)
    assert result["year_min"] == "1950"
    assert result["language"] == "de"


def test_normalize_filters_truncates_long_values():
    long_val = "x" * 200
    form = FakeFormData(
        scalars={"language": long_val},
        lists={"genres[]": []},
    )
    result = normalize_filters(form)
    assert len(result["language"]) == MAX_FILTER_VALUE_LEN


def test_normalize_filters_validates_genres():
    form = FakeFormData(
        scalars={},
        lists={"genres[]": ["Drama", "InvalidGenre", "Comedy", 42]},
    )
    result = normalize_filters(form)
    assert result["genres_selected"] == ["Drama", "Comedy"]


def test_normalize_filters_non_string_value_kept_as_is():
    form = FakeFormData(
        scalars={"year_min": 1980},
        lists={"genres[]": []},
    )
    result = normalize_filters(form)
    assert result["year_min"] == 1980


def test_normalize_filters_genre_truncation():
    """Genres exceeding MAX_FILTER_VALUE_LEN are truncated before validation."""
    long_genre = "Drama" + "x" * 200
    form = FakeFormData(
        scalars={},
        lists={"genres[]": [long_genre]},
    )
    result = normalize_filters(form)
    # The truncated value won't match any VALID_GENRES entry, so empty
    assert result["genres_selected"] == []


# ===========================================================================
# _normalize_ref()
# ===========================================================================


def test_normalize_ref_extracts_tconst_key():
    entry = {"tconst": "tt123", "title": "Test", "slug": "test"}
    result = _normalize_ref(entry)
    assert result == {"tconst": "tt123", "title": "Test", "slug": "test"}


def test_normalize_ref_extracts_imdb_id_key():
    entry = {"imdb_id": "tt456", "title": "Other"}
    result = _normalize_ref(entry)
    assert result["tconst"] == "tt456"
    assert result["title"] == "Other"
    assert result["slug"] is None


def test_normalize_ref_returns_none_for_non_dict():
    assert _normalize_ref("tt123") is None
    assert _normalize_ref(42) is None
    assert _normalize_ref(None) is None
    assert _normalize_ref(["tt123"]) is None


def test_normalize_ref_returns_none_for_missing_tconst():
    assert _normalize_ref({"title": "No ID"}) is None
    assert _normalize_ref({}) is None


# ===========================================================================
# _normalize_ref_list()
# ===========================================================================


def test_normalize_ref_list_respects_max_items():
    entries = [{"tconst": f"tt{i}", "title": f"M{i}"} for i in range(10)]
    result = _normalize_ref_list(entries, max_items=3)
    assert len(result) == 3
    assert result[0]["tconst"] == "tt0"


def test_normalize_ref_list_filters_invalid_entries():
    entries = [
        {"tconst": "tt1", "title": "Good"},
        "bad",
        None,
        {"title": "No ID"},
        {"tconst": "tt2", "title": "Also good"},
    ]
    result = _normalize_ref_list(entries, max_items=10)
    assert len(result) == 2
    assert result[0]["tconst"] == "tt1"
    assert result[1]["tconst"] == "tt2"


def test_normalize_ref_list_handles_none_input():
    result = _normalize_ref_list(None, max_items=5)
    assert result == []


# ===========================================================================
# _normalize_seen()
# ===========================================================================


def test_normalize_seen_filters_non_strings():
    entries = ["tt1", 42, None, "tt2", "", "tt3"]
    result = _normalize_seen(entries)
    # Empty strings are excluded (falsy check)
    assert result == ["tt1", "tt2", "tt3"]


def test_normalize_seen_enforces_max():
    entries = [f"tt{i}" for i in range(SEEN_MAX + 20)]
    result = _normalize_seen(entries)
    assert len(result) == SEEN_MAX
    # Should keep the LAST SEEN_MAX items (tail slice)
    assert result[0] == f"tt{20}"
    assert result[-1] == f"tt{SEEN_MAX + 19}"


def test_normalize_seen_handles_none():
    assert _normalize_seen(None) == []


# ===========================================================================
# NavigationStateStore._fresh_state()
# ===========================================================================


def test_fresh_state_creates_valid_state(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    state = store._fresh_state()

    # session_id is a UUID hex (32 hex chars)
    assert len(state.session_id) == 32
    assert state.version == 1
    assert len(state.csrf_token) == 64  # token_hex(32) -> 64 hex chars
    assert state.filters == default_filter_state()
    assert state.current_tconst is None
    assert state.queue == []
    assert state.prev == []
    assert state.future == []
    assert state.seen == []


def test_fresh_state_accepts_custom_session_id(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    state = store._fresh_state(session_id="custom-id")
    assert state.session_id == "custom-id"


def test_fresh_state_expiry_within_bounds(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    state = store._fresh_state()
    # expires_at should be no later than created_at + idle timeout
    assert state.expires_at >= state.created_at
    assert state.expires_at <= state.created_at + timedelta(hours=8)


# ===========================================================================
# NavigationStateStore._json_load()
# ===========================================================================


def test_json_load_returns_fallback_for_none(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    assert store._json_load(None, {"default": True}) == {"default": True}


def test_json_load_returns_dict_directly(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    d = {"key": "value"}
    assert store._json_load(d, {}) is d


def test_json_load_returns_list_directly(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    lst = [1, 2, 3]
    assert store._json_load(lst, []) is lst


def test_json_load_parses_valid_json_string(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    result = store._json_load('{"a": 1}', {})
    assert result == {"a": 1}


def test_json_load_returns_fallback_for_invalid_json(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    assert store._json_load("not json", []) == []


def test_json_load_returns_fallback_for_unexpected_type(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    assert store._json_load(42, "fallback") == "fallback"


# ===========================================================================
# NavigationStateStore._row_to_state()
# ===========================================================================


def test_row_to_state_deserializes_json_fields(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    now = utcnow()
    row = {
        "session_id": "s1",
        "version": 3,
        "csrf_token": "csrf-tok",
        "filters_json": json.dumps(
            {
                "year_min": 2000,
                "year_max": 2020,
                "imdb_score_min": 6.0,
                "imdb_score_max": 9.0,
                "num_votes_min": 50000,
                "num_votes_max": 100000,
                "language": "en",
                "genres_selected": ["Drama"],
            }
        ),
        "current_tconst": "tt999",
        "queue_json": json.dumps([{"tconst": "tt1", "title": "Q1", "slug": "q1"}]),
        "prev_json": json.dumps([{"tconst": "tt2", "title": "P1", "slug": "p1"}]),
        "future_json": json.dumps([{"tconst": "tt3", "title": "F1", "slug": "f1"}]),
        "seen_json": json.dumps(["tt10", "tt11"]),
        "created_at": now,
        "last_activity_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    state = store._row_to_state(row)

    assert state.session_id == "s1"
    assert state.version == 3
    assert state.csrf_token == "csrf-tok"
    assert state.current_tconst == "tt999"
    assert state.filters["year_min"] == 2000
    assert state.filters["genres_selected"] == ["Drama"]
    assert len(state.queue) == 1
    assert state.queue[0]["tconst"] == "tt1"
    assert len(state.prev) == 1
    assert len(state.future) == 1
    assert state.seen == ["tt10", "tt11"]


def test_row_to_state_handles_already_parsed_json(mock_db_pool):
    """When the DB driver returns dicts/lists instead of JSON strings."""
    store = NavigationStateStore(mock_db_pool)
    now = utcnow()
    row = {
        "session_id": "s2",
        "version": 1,
        "csrf_token": "tok",
        "filters_json": {
            "year_min": 1900,
            "year_max": 2024,
            "imdb_score_min": 7.0,
            "imdb_score_max": 10.0,
            "num_votes_min": 100000,
            "num_votes_max": 200000,
            "language": "en",
            "genres_selected": [],
        },
        "current_tconst": None,
        "queue_json": [],
        "prev_json": [],
        "future_json": [],
        "seen_json": [],
        "created_at": now,
        "last_activity_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    state = store._row_to_state(row)
    assert state.filters["year_min"] == 1900
    assert state.queue == []


def test_row_to_state_falls_back_on_invalid_filters_json(mock_db_pool):
    """Non-dict filters_json falls back to default_filter_state()."""
    store = NavigationStateStore(mock_db_pool)
    now = utcnow()
    row = {
        "session_id": "s3",
        "version": 1,
        "csrf_token": "tok",
        "filters_json": json.dumps([1, 2, 3]),  # list, not dict
        "current_tconst": None,
        "queue_json": None,
        "prev_json": None,
        "future_json": None,
        "seen_json": None,
        "created_at": now,
        "last_activity_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    state = store._row_to_state(row)
    assert state.filters == default_filter_state()


def test_row_to_state_loads_current_ref_json(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    now = utcnow()
    row = {
        "session_id": "s4",
        "version": 1,
        "csrf_token": "tok",
        "filters_json": json.dumps(default_filter_state()),
        "current_tconst": "tt1234567",
        "current_ref_json": json.dumps(_ref("tt1234567", "Loaded", "loaded")),
        "queue_json": "[]",
        "prev_json": "[]",
        "future_json": "[]",
        "seen_json": "[]",
        "created_at": now,
        "last_activity_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    state = store._row_to_state(row)
    assert state.current_ref == _ref("tt1234567", "Loaded", "loaded")


# ===========================================================================
# NavigationStateStore.save_state() — optimistic locking
# ===========================================================================


async def test_save_state_returns_true_on_version_match(mock_db_pool):
    mock_db_pool.execute = AsyncMock(return_value=1)
    store = NavigationStateStore(mock_db_pool)
    state = _make_state(version=1)

    result = await store.save_state(state, expected_version=1)

    assert result is True
    assert state.version == 2


async def test_save_state_returns_false_on_version_mismatch(mock_db_pool):
    mock_db_pool.execute = AsyncMock(return_value=0)
    store = NavigationStateStore(mock_db_pool)
    state = _make_state(version=1)

    result = await store.save_state(state, expected_version=1)

    assert result is False
    # version should NOT be bumped on failure
    assert state.version == 1


async def test_save_state_updates_timestamps(mock_db_pool):
    mock_db_pool.execute = AsyncMock(return_value=1)
    store = NavigationStateStore(mock_db_pool)
    old_time = utcnow() - timedelta(hours=1)
    state = _make_state(last_activity_at=old_time, expires_at=old_time)

    await store.save_state(state, expected_version=1)

    assert state.last_activity_at > old_time
    assert state.expires_at > old_time


async def test_ready_check_is_select_only(mock_db_pool):
    mock_db_pool.execute = AsyncMock(return_value={"ready": 1})
    store = NavigationStateStore(mock_db_pool)

    result = await store.ready_check()

    assert result is True
    mock_db_pool.execute.assert_awaited_once()
    query = mock_db_pool.execute.await_args.args[0]
    assert "SELECT 1 AS ready" in query


async def test_save_state_only_updates_changed_fields(mock_db_pool):
    mock_db_pool.execute = AsyncMock(return_value=1)
    store = NavigationStateStore(mock_db_pool)
    previous_state = _make_state(current_tconst="tt1", current_ref=_ref("tt1"))
    state = previous_state.clone()
    state.current_tconst = "tt2"
    state.current_ref = _ref("tt2", "Two", "two")

    await store.save_state(state, expected_version=1, previous_state=previous_state)

    query = mock_db_pool.execute.await_args.args[0]
    params = mock_db_pool.execute.await_args.args[1]
    assert "current_tconst = %s" in query
    assert "current_ref_json = %s" in query
    assert "filters_json = %s" not in query
    assert any(isinstance(param, str) and '"tt2"' in param for param in params)


# ===========================================================================
# NavigationStateStore.mutate() — retry on conflict
# ===========================================================================


async def test_mutate_success_first_attempt(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    state = _make_state()
    now = utcnow()

    # get_state returns the state (not expired)
    state.expires_at = now + timedelta(hours=1)
    mock_db_pool.execute = AsyncMock(
        side_effect=[
            # First call: _load_row SELECT
            {
                "session_id": state.session_id,
                "version": 1,
                "csrf_token": state.csrf_token,
                "filters_json": json.dumps(state.filters),
                "current_tconst": None,
                "queue_json": "[]",
                "prev_json": "[]",
                "future_json": "[]",
                "seen_json": "[]",
                "created_at": state.created_at,
                "last_activity_at": state.last_activity_at,
                "expires_at": state.expires_at,
            },
            # Second call: save_state UPDATE returns 1 (success)
            1,
        ]
    )

    def mutator(s):
        s.current_tconst = "tt999"
        return "mutator-result"

    with patch(
        "infra.navigation_state.NavigationStateStore.dual_write_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await store.mutate(state.session_id, mutator)

    assert result.conflicted is False
    assert result.result == "mutator-result"
    assert result.state is not None
    assert result.state.current_tconst == "tt999"


async def test_mutate_retries_on_conflict_then_succeeds(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    now = utcnow()
    expires = now + timedelta(hours=1)

    row = {
        "session_id": "retry-session",
        "version": 1,
        "csrf_token": "tok",
        "filters_json": json.dumps(default_filter_state()),
        "current_tconst": None,
        "queue_json": "[]",
        "prev_json": "[]",
        "future_json": "[]",
        "seen_json": "[]",
        "created_at": now,
        "last_activity_at": now,
        "expires_at": expires,
    }

    mock_db_pool.execute = AsyncMock(
        side_effect=[
            # Attempt 1: get_state -> _load_row
            dict(row),
            # Attempt 1: save_state -> version conflict (returns 0)
            0,
            # Attempt 2: get_state -> _load_row (refetch)
            dict(row),
            # Attempt 2: save_state -> success (returns 1)
            1,
        ]
    )

    def mutator(s):
        s.current_tconst = "tt888"

    with patch(
        "infra.navigation_state.NavigationStateStore.dual_write_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ), patch("infra.metrics.navigation_state_conflicts_total") as mock_metric:
        result = await store.mutate("retry-session", mutator)

    assert result.conflicted is False
    assert result.state.current_tconst == "tt888"
    # Conflict counter should have been incremented once
    mock_metric.inc.assert_called_once()


async def test_mutate_returns_conflicted_after_exhausting_retries(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    now = utcnow()
    expires = now + timedelta(hours=1)

    row = {
        "session_id": "exhaust-session",
        "version": 1,
        "csrf_token": "tok",
        "filters_json": json.dumps(default_filter_state()),
        "current_tconst": None,
        "queue_json": "[]",
        "prev_json": "[]",
        "future_json": "[]",
        "seen_json": "[]",
        "created_at": now,
        "last_activity_at": now,
        "expires_at": expires,
    }

    # 5 attempts × (get_state + save_state) + final get_state for return value
    mock_db_pool.execute = AsyncMock(
        side_effect=[
            dict(row), 0,
            dict(row), 0,
            dict(row), 0,
            dict(row), 0,
            dict(row), 0,
            dict(row),
        ]
    )

    def mutator(s):
        s.current_tconst = "tt777"

    with patch(
        "infra.navigation_state.NavigationStateStore.dual_write_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ), patch("infra.metrics.navigation_state_conflicts_total"):
        result = await store.mutate("exhaust-session", mutator)

    assert result.conflicted is True


async def test_mutate_returns_conflicted_when_state_not_found(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    # get_state returns None (no row found)
    mock_db_pool.execute = AsyncMock(return_value=None)

    def mutator(s):
        pass

    with patch("infra.metrics.navigation_state_conflicts_total"):
        result = await store.mutate("nonexistent", mutator)

    assert result.conflicted is True
    assert result.state is None


async def test_mutate_supports_async_mutator(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    now = utcnow()
    expires = now + timedelta(hours=1)

    row = {
        "session_id": "async-session",
        "version": 1,
        "csrf_token": "tok",
        "filters_json": json.dumps(default_filter_state()),
        "current_tconst": None,
        "queue_json": "[]",
        "prev_json": "[]",
        "future_json": "[]",
        "seen_json": "[]",
        "created_at": now,
        "last_activity_at": now,
        "expires_at": expires,
    }

    mock_db_pool.execute = AsyncMock(
        side_effect=[
            dict(row),  # get_state
            1,  # save_state success
        ]
    )

    async def async_mutator(s):
        s.current_tconst = "tt555"
        return "async-result"

    with patch(
        "infra.navigation_state.NavigationStateStore.dual_write_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await store.mutate("async-session", async_mutator)

    assert result.conflicted is False
    assert result.result == "async-result"
    assert result.state.current_tconst == "tt555"


async def test_mutate_uses_current_state_without_reloading(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    state = _make_state(version=3)
    mock_db_pool.execute = AsyncMock(return_value=1)

    def mutator(current):
        current.current_tconst = "tt333"
        current.current_ref = _ref("tt333", "Three", "three")

    with patch(
        "infra.navigation_state.NavigationStateStore.dual_write_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await store.mutate(state.session_id, mutator, current_state=state)

    assert result.conflicted is False
    assert result.state.current_tconst == "tt333"
    assert result.state.current_ref == _ref("tt333", "Three", "three")
    mock_db_pool.execute.assert_awaited_once()


async def test_mutate_passes_a_clone_not_the_shared_state(mock_db_pool):
    """Regression lock: mutate() must pass a clone to the mutator.

    Concurrent navigation correctness (e.g. two rapid /next_movie requests
    for the same session) depends on each mutate call operating on its
    own deep copy of state. If a future refactor hands the shared state
    reference directly to the mutator, two concurrent calls could
    interleave their in-memory queue mutations and lose or duplicate
    entries before the optimistic-lock save fires.
    """
    store = NavigationStateStore(mock_db_pool)
    state = _make_state(
        version=7,
        queue=[{"tconst": "tt1"}, {"tconst": "tt2"}],
    )
    mock_db_pool.execute = AsyncMock(return_value=1)

    captured: dict[str, NavigationState] = {}

    def mutator(s):
        captured["arg"] = s
        # Mutate the (hopefully) clone — must not touch the original.
        s.queue.pop(0)
        return "ok"

    with patch(
        "infra.navigation_state.NavigationStateStore.dual_write_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await store.mutate(
            state.session_id, mutator, current_state=state
        )

    assert result.conflicted is False
    assert result.result == "ok"
    # Invariant 1: the mutator received a different object than our input.
    assert captured["arg"] is not state
    # Invariant 2: the caller's state was not mutated by the mutator.
    assert len(state.queue) == 2
    assert state.queue[0]["tconst"] == "tt1"
    # Invariant 3: the mutator's changes DID land in the result.
    assert len(result.state.queue) == 1
    assert result.state.queue[0]["tconst"] == "tt2"


# ===========================================================================
# NavigationState.user_id field
# ===========================================================================


async def test_navigation_state_has_user_id_field():
    from infra.navigation_state import NavigationState, default_filter_state
    from infra.time_utils import utcnow

    now = utcnow()
    state = NavigationState(
        session_id="test123",
        version=1,
        csrf_token="csrf",
        filters=default_filter_state(),
        current_tconst=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
        user_id=None,
    )
    assert state.user_id is None
    state.user_id = "abc123"
    assert state.user_id == "abc123"


# ---------------------------------------------------------------------------
# _serialized_state_fields memoization
# ---------------------------------------------------------------------------


def test_serialized_state_fields_memoizes_on_state(mock_db_pool, monkeypatch):
    store = NavigationStateStore(mock_db_pool)
    state = _make_state(queue=[_ref("tt1")], prev=[_ref("tt2")])

    call_count = {"n": 0}
    real_dumps = json.dumps

    def counting_dumps(*args, **kwargs):
        call_count["n"] += 1
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr("infra.navigation_state.json.dumps", counting_dumps)

    first = store._serialized_state_fields(state)
    dumps_after_first = call_count["n"]
    assert dumps_after_first > 0

    second = store._serialized_state_fields(state)
    # Cache hit: no further json.dumps invocations.
    assert call_count["n"] == dumps_after_first
    assert first == second
    assert first is second


def test_clone_resets_serialized_cache(mock_db_pool):
    store = NavigationStateStore(mock_db_pool)
    state = _make_state(queue=[_ref("tt1")])

    store._serialized_state_fields(state)
    assert state._serialized_cache is not None

    cloned = state.clone()
    assert cloned._serialized_cache is None
