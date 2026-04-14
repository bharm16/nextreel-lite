"""Tests for movies.watched_store — WatchedStore data-access layer."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from movies.watched_store import WatchedStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(mock_db_pool) -> WatchedStore:
    return WatchedStore(mock_db_pool)


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


async def test_add_executes_insert_with_correct_params(mock_db_pool):
    """add() inserts (user_id, tconst, watched_at) with fetch='none'."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.add("user-1", "tt1234567")

    mock_db_pool.execute.assert_awaited_once()
    call_args = mock_db_pool.execute.call_args
    query, params = call_args[0][0], call_args[0][1]
    assert "INSERT INTO user_watched_movies" in query
    assert "ON DUPLICATE KEY UPDATE" in query
    assert params[0] == "user-1"
    assert params[1] == "tt1234567"
    assert call_args[1]["fetch"] == "none"


async def test_add_is_idempotent_via_on_duplicate_key(mock_db_pool):
    """add() uses ON DUPLICATE KEY UPDATE so calling twice doesn't raise."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.add("user-1", "tt1234567")
    await store.add("user-1", "tt1234567")

    assert mock_db_pool.execute.await_count == 2


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


async def test_remove_executes_delete_with_correct_params(mock_db_pool):
    """remove() sends a DELETE with the right user_id and tconst."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.remove("user-1", "tt1234567")

    mock_db_pool.execute.assert_awaited_once()
    call_args = mock_db_pool.execute.call_args
    query, params = call_args[0][0], call_args[0][1]
    assert "DELETE FROM user_watched_movies" in query
    assert "user_id = %s" in query
    assert "tconst = %s" in query
    assert params == ["user-1", "tt1234567"]
    assert call_args[1]["fetch"] == "none"


# ---------------------------------------------------------------------------
# is_watched
# ---------------------------------------------------------------------------


async def test_is_watched_returns_true_when_row_exists(mock_db_pool):
    """is_watched() returns True when the DB returns a row."""
    mock_db_pool.execute.return_value = {"cnt": 1}
    store = _make_store(mock_db_pool)

    result = await store.is_watched("user-1", "tt1234567")

    assert result is True


async def test_is_watched_returns_false_when_no_row(mock_db_pool):
    """is_watched() returns False when the DB returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    result = await store.is_watched("user-1", "tt9999999")

    assert result is False


async def test_is_watched_uses_fetch_one(mock_db_pool):
    """is_watched() queries with fetch='one'."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.is_watched("user-1", "tt1234567")

    call_args = mock_db_pool.execute.call_args
    assert call_args[1]["fetch"] == "one"


async def test_is_watched_passes_correct_params(mock_db_pool):
    """is_watched() passes [user_id, tconst] as query params."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.is_watched("user-abc", "tt0000001")

    call_args = mock_db_pool.execute.call_args
    assert call_args[0][1] == ["user-abc", "tt0000001"]


# ---------------------------------------------------------------------------
# watched_tconsts
# ---------------------------------------------------------------------------


async def test_watched_tconsts_returns_set_of_tconsts(mock_db_pool):
    """watched_tconsts() returns a set of tconst strings from DB rows."""
    mock_db_pool.execute.return_value = [
        {"tconst": "tt0000001"},
        {"tconst": "tt0000002"},
        {"tconst": "tt0000003"},
    ]
    store = _make_store(mock_db_pool)

    result = await store.watched_tconsts("user-1")

    assert result == {"tt0000001", "tt0000002", "tt0000003"}


async def test_watched_tconsts_returns_empty_set_when_no_rows(mock_db_pool):
    """watched_tconsts() returns an empty set when the user has no watched movies."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    result = await store.watched_tconsts("user-1")

    assert result == set()


async def test_watched_tconsts_returns_empty_set_when_none(mock_db_pool):
    """watched_tconsts() returns an empty set when DB returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    result = await store.watched_tconsts("user-1")

    assert result == set()


async def test_watched_tconsts_uses_fetch_all(mock_db_pool):
    """watched_tconsts() queries with fetch='all'."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.watched_tconsts("user-1")

    call_args = mock_db_pool.execute.call_args
    assert call_args[1]["fetch"] == "all"


async def test_watched_tconsts_passes_user_id(mock_db_pool):
    """watched_tconsts() passes [user_id] as query params."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.watched_tconsts("user-xyz")

    call_args = mock_db_pool.execute.call_args
    assert call_args[0][1] == ["user-xyz"]


# ---------------------------------------------------------------------------
# watched_tconsts caching
# ---------------------------------------------------------------------------


class _FakeCache:
    """Minimal in-memory stand-in for SimpleCacheManager."""

    def __init__(self):
        self.store: dict[tuple, object] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.delete_calls = 0

    async def get(self, namespace, key):
        self.get_calls += 1
        return self.store.get((namespace, key))

    async def set(self, namespace, key, value, ttl=None):
        self.set_calls += 1
        self.store[(namespace, key)] = value

    async def delete(self, namespace, key):
        self.delete_calls += 1
        self.store.pop((namespace, key), None)

    async def safe_get_or_set(self, namespace, key, loader, ttl=None):
        cached = await self.get(namespace, key)
        if cached is not None:
            return cached
        value = await loader()
        if value is not None:
            await self.set(namespace, key, value, ttl=ttl)
        return value


async def test_watched_tconsts_uses_cache_on_second_call(mock_db_pool):
    """The second call hits the cache and skips the DB."""
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}, {"tconst": "tt2"}]
    cache = _FakeCache()
    store = WatchedStore(mock_db_pool, cache=cache)

    first = await store.watched_tconsts("user-1")
    second = await store.watched_tconsts("user-1")

    assert first == {"tt1", "tt2"}
    assert second == {"tt1", "tt2"}
    # DB queried only once; cache served the second call.
    assert mock_db_pool.execute.await_count == 1
    assert cache.set_calls == 1


async def test_add_invalidates_cache(mock_db_pool):
    """add() drops the cached set so the next read re-queries the DB."""
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}]
    cache = _FakeCache()
    store = WatchedStore(mock_db_pool, cache=cache)

    await store.watched_tconsts("user-1")  # populate cache
    await store.add("user-1", "tt2")
    assert cache.delete_calls == 1


async def test_remove_invalidates_cache(mock_db_pool):
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}]
    cache = _FakeCache()
    store = WatchedStore(mock_db_pool, cache=cache)

    await store.watched_tconsts("user-1")
    await store.remove("user-1", "tt1")
    assert cache.delete_calls == 1


async def test_watched_tconsts_works_without_cache(mock_db_pool):
    """No cache configured -> falls through to DB on every call."""
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}]
    store = WatchedStore(mock_db_pool)

    await store.watched_tconsts("user-1")
    await store.watched_tconsts("user-1")
    assert mock_db_pool.execute.await_count == 2


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


async def test_count_returns_integer_count(mock_db_pool):
    """count() returns the cnt value from the DB row."""
    mock_db_pool.execute.return_value = {"cnt": 42}
    store = _make_store(mock_db_pool)

    result = await store.count("user-1")

    assert result == 42


async def test_count_returns_zero_when_no_row(mock_db_pool):
    """count() returns 0 when DB returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    result = await store.count("user-1")

    assert result == 0


async def test_count_uses_fetch_one(mock_db_pool):
    """count() queries with fetch='one'."""
    mock_db_pool.execute.return_value = {"cnt": 0}
    store = _make_store(mock_db_pool)

    await store.count("user-1")

    call_args = mock_db_pool.execute.call_args
    assert call_args[1]["fetch"] == "one"


async def test_count_passes_user_id(mock_db_pool):
    """count() passes [user_id] as query params."""
    mock_db_pool.execute.return_value = {"cnt": 0}
    store = _make_store(mock_db_pool)

    await store.count("user-abc")

    call_args = mock_db_pool.execute.call_args
    assert call_args[0][1] == ["user-abc"]


async def test_count_uses_count_star_query(mock_db_pool):
    """count() executes a COUNT(*) query against user_watched_movies."""
    mock_db_pool.execute.return_value = {"cnt": 0}
    store = _make_store(mock_db_pool)

    await store.count("user-1")

    query = mock_db_pool.execute.call_args[0][0]
    assert "COUNT(*)" in query
    assert "user_watched_movies" in query


# ---------------------------------------------------------------------------
# list_watched
# ---------------------------------------------------------------------------


async def test_list_watched_returns_rows_from_db(mock_db_pool):
    """list_watched() returns the list of dicts from the DB."""
    rows = [
        {
            "tconst": "tt0000001",
            "watched_at": "2024-01-01T12:00:00",
            "primaryTitle": "Movie One",
            "startYear": 2020,
            "genres": "Action,Drama",
            "slug": "movie-one",
        },
        {
            "tconst": "tt0000002",
            "watched_at": "2024-01-02T12:00:00",
            "primaryTitle": "Movie Two",
            "startYear": 2021,
            "genres": "Comedy",
            "slug": "movie-two",
        },
    ]
    mock_db_pool.execute.return_value = rows
    store = _make_store(mock_db_pool)

    result = await store.list_watched("user-1")

    assert result == rows
    assert len(result) == 2
    assert result[0]["tconst"] == "tt0000001"


async def test_list_watched_returns_empty_list_when_no_rows(mock_db_pool):
    """list_watched() returns [] when DB returns an empty list."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    result = await store.list_watched("user-1")

    assert result == []


async def test_list_watched_returns_empty_list_when_none(mock_db_pool):
    """list_watched() returns [] when DB returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    result = await store.list_watched("user-1")

    assert result == []


async def test_list_watched_passes_limit_and_offset(mock_db_pool):
    """list_watched() passes user_id, limit, and offset as query params."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched("user-1", limit=10, offset=20)

    call_args = mock_db_pool.execute.call_args
    params = call_args[0][1]
    assert params == ["user-1", 10, 20]


async def test_list_watched_uses_fetch_all(mock_db_pool):
    """list_watched() queries with fetch='all'."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched("user-1")

    call_args = mock_db_pool.execute.call_args
    assert call_args[1]["fetch"] == "all"


async def test_list_watched_uses_default_limit_and_offset(mock_db_pool):
    """list_watched() defaults to limit=20, offset=0."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched("user-1")

    params = mock_db_pool.execute.call_args[0][1]
    assert params[1] == 20
    assert params[2] == 0


async def test_list_watched_orders_by_watched_at_desc(mock_db_pool):
    """list_watched() uses ORDER BY watched_at DESC for recency ordering."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched("user-1")

    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.watched_at DESC" in query


async def test_list_watched_joins_movie_candidates(mock_db_pool):
    """list_watched() LEFT JOINs movie_candidates for metadata."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched("user-1")

    query = mock_db_pool.execute.call_args[0][0]
    assert "LEFT JOIN movie_candidates" in query


# ---------------------------------------------------------------------------
# add_bulk
# ---------------------------------------------------------------------------


async def test_add_bulk_executes_multi_value_insert(mock_db_pool):
    """add_bulk() builds a multi-value INSERT with ON DUPLICATE KEY."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    count = await store.add_bulk("user-1", ["tt0000001", "tt0000002", "tt0000003"])

    assert count == 3
    call_args = mock_db_pool.execute.call_args
    query = call_args[0][0]
    assert "INSERT INTO user_watched_movies" in query
    assert "ON DUPLICATE KEY UPDATE" in query
    assert query.count("(%s, %s, %s)") == 3


async def test_add_bulk_empty_list_returns_zero(mock_db_pool):
    """add_bulk() with empty list does nothing."""
    store = _make_store(mock_db_pool)

    count = await store.add_bulk("user-1", [])

    assert count == 0
    mock_db_pool.execute.assert_not_awaited()


async def test_add_bulk_invalidates_cache(mock_db_pool):
    """add_bulk() invalidates watched cache after insert."""
    mock_db_pool.execute.return_value = None
    mock_cache = AsyncMock()
    store = _make_store(mock_db_pool)
    store.attach_cache(mock_cache)

    await store.add_bulk("user-1", ["tt0000001"])

    mock_cache.delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# import enrichment progress lookups
# ---------------------------------------------------------------------------


async def test_ready_tconsts_for_import_queries_ready_projection_rows(mock_db_pool):
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}, {"tconst": "tt2"}]
    store = WatchedStore(mock_db_pool)

    ready = await store.ready_tconsts_for_import(["tt1", "tt2", "tt3"])

    assert ready == {"tt1", "tt2"}
    query, params = mock_db_pool.execute.call_args[0][0], mock_db_pool.execute.call_args[0][1]
    assert "FROM movie_projection" in query
    assert "projection_state = %s" in query
    assert params == ["tt1", "tt2", "tt3", "ready"]


async def test_ready_tconsts_for_import_empty_input_skips_db(mock_db_pool):
    store = WatchedStore(mock_db_pool)

    ready = await store.ready_tconsts_for_import([])

    assert ready == set()
    mock_db_pool.execute.assert_not_awaited()


async def test_ready_import_rows_queries_ready_projection_rows(mock_db_pool):
    mock_db_pool.execute.return_value = [{"tconst": "tt1", "primaryTitle": "Inception"}]
    store = WatchedStore(mock_db_pool)

    rows = await store.ready_import_rows("user-1", ["tt1"])

    assert rows == [{"tconst": "tt1", "primaryTitle": "Inception"}]
    query, params = mock_db_pool.execute.call_args[0][0], mock_db_pool.execute.call_args[0][1]
    assert "INNER JOIN movie_projection" in query
    assert "projection_state = %s" in query
    assert params == ["tt1", "user-1", "ready"]


async def test_ready_import_rows_empty_input_skips_db(mock_db_pool):
    store = WatchedStore(mock_db_pool)

    rows = await store.ready_import_rows("user-1", [])

    assert rows == []
    mock_db_pool.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_watched_filtered
# ---------------------------------------------------------------------------


async def test_list_watched_filtered_default_sort_recent(mock_db_pool):
    """list_watched_filtered() defaults to ORDER BY w.watched_at DESC."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0)
    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.watched_at DESC" in query


async def test_list_watched_filtered_sort_title_az(mock_db_pool):
    """list_watched_filtered() with sort='title_asc' orders A-Z."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="title_asc", limit=60, offset=0)
    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY c.primaryTitle ASC" in query


async def test_list_watched_filtered_sort_title_za(mock_db_pool):
    """list_watched_filtered() with sort='title_desc' orders Z-A."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="title_desc", limit=60, offset=0)
    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY c.primaryTitle DESC" in query


async def test_list_watched_filtered_sort_year_desc(mock_db_pool):
    """list_watched_filtered() with sort='year_desc' orders newest first."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="year_desc", limit=60, offset=0)
    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY c.startYear DESC" in query


async def test_list_watched_filtered_sort_rating_desc(mock_db_pool):
    """list_watched_filtered() with sort='rating_desc' orders highest first."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="rating_desc", limit=60, offset=0)
    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY" in query
    assert "rating" in query.lower() or "averageRating" in query


async def test_list_watched_filtered_decade_filter(mock_db_pool):
    """list_watched_filtered() with decades=['2020'] filters to 2020-2029."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0, decades=["2020"])
    query = mock_db_pool.execute.call_args[0][0]
    params = mock_db_pool.execute.call_args[0][1]
    assert "c.startYear >=" in query
    assert 2020 in params
    assert 2029 in params


async def test_list_watched_filtered_multiple_decades(mock_db_pool):
    """list_watched_filtered() with decades=['2020','2010'] uses OR within decade."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0, decades=["2020", "2010"])
    params = mock_db_pool.execute.call_args[0][1]
    assert 2020 in params
    assert 2029 in params
    assert 2010 in params
    assert 2019 in params


async def test_list_watched_filtered_rating_filter(mock_db_pool):
    """list_watched_filtered() with rating_min filters by averageRating."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0, rating_min=8.0, rating_max=10.0)
    query = mock_db_pool.execute.call_args[0][0]
    assert "averageRating" in query


async def test_list_watched_filtered_genre_filter(mock_db_pool):
    """list_watched_filtered() with genres=['Horror'] filters by genre."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0, genres=["Horror"])
    query = mock_db_pool.execute.call_args[0][0]
    assert "genres" in query.lower()


async def test_list_watched_filtered_combined_filters(mock_db_pool):
    """list_watched_filtered() applies decade AND genre filters together."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0, decades=["2020"], genres=["Horror"])
    query = mock_db_pool.execute.call_args[0][0]
    assert "genres" in query.lower()
    assert "startYear" in query


async def test_list_watched_filtered_returns_rows(mock_db_pool):
    """list_watched_filtered() returns the row list from DB."""
    rows = [{"tconst": "tt1", "primaryTitle": "Test", "startYear": 2024, "genres": "Drama"}]
    mock_db_pool.execute.return_value = rows
    store = _make_store(mock_db_pool)
    result = await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0)
    assert result == rows


async def test_list_watched_filtered_returns_empty_on_none(mock_db_pool):
    """list_watched_filtered() returns [] when DB returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)
    result = await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0)
    assert result == []


async def test_list_watched_filtered_passes_limit_offset(mock_db_pool):
    """list_watched_filtered() passes limit and offset as query params."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="recent", limit=30, offset=60)
    params = mock_db_pool.execute.call_args[0][1]
    assert params[-2] == 30
    assert params[-1] == 60


async def test_list_watched_filtered_invalid_sort_falls_back(mock_db_pool):
    """list_watched_filtered() with invalid sort falls back to recent."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    await store.list_watched_filtered("user-1", sort="invalid_sort", limit=60, offset=0)
    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.watched_at DESC" in query


# ---------------------------------------------------------------------------
# count_filtered
# ---------------------------------------------------------------------------


async def test_count_filtered_no_filters(mock_db_pool):
    """count_filtered() without filters returns total watched count."""
    mock_db_pool.execute.return_value = {"cnt": 100}
    store = _make_store(mock_db_pool)
    result = await store.count_filtered("user-1")
    assert result == 100


async def test_count_filtered_with_decade(mock_db_pool):
    """count_filtered() with decade filter includes decade WHERE clause."""
    mock_db_pool.execute.return_value = {"cnt": 42}
    store = _make_store(mock_db_pool)
    result = await store.count_filtered("user-1", decades=["2020"])
    assert result == 42
    query = mock_db_pool.execute.call_args[0][0]
    assert "startYear" in query


async def test_count_filtered_returns_zero_on_none(mock_db_pool):
    """count_filtered() returns 0 when DB returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)
    result = await store.count_filtered("user-1")
    assert result == 0


# ---------------------------------------------------------------------------
# available_filter_chips
# ---------------------------------------------------------------------------


async def test_available_filter_chips_returns_decades(mock_db_pool):
    """available_filter_chips() returns decade labels from startYear values."""
    mock_db_pool.execute.return_value = [
        {"startYear": 2024, "genres": "Drama", "averageRating": 7.5},
        {"startYear": 2015, "genres": "Horror,Comedy", "averageRating": 8.2},
        {"startYear": 2023, "genres": "Drama", "averageRating": 5.0},
    ]
    store = _make_store(mock_db_pool)
    chips = await store.available_filter_chips("user-1")
    assert "2020s" in chips["decades"]
    assert "2010s" in chips["decades"]


async def test_available_filter_chips_returns_genres(mock_db_pool):
    """available_filter_chips() returns unique genres from CSV genre column."""
    mock_db_pool.execute.return_value = [
        {"startYear": 2024, "genres": "Drama,Horror", "averageRating": 7.5},
        {"startYear": 2015, "genres": "Horror,Comedy", "averageRating": 8.2},
    ]
    store = _make_store(mock_db_pool)
    chips = await store.available_filter_chips("user-1")
    assert "Drama" in chips["genres"]
    assert "Horror" in chips["genres"]
    assert "Comedy" in chips["genres"]


async def test_available_filter_chips_returns_rating_tiers(mock_db_pool):
    """available_filter_chips() returns rating tiers that have >=1 film."""
    mock_db_pool.execute.return_value = [
        {"startYear": 2024, "genres": "Drama", "averageRating": 8.5},
        {"startYear": 2024, "genres": "Drama", "averageRating": 7.0},
        {"startYear": 2024, "genres": "Drama", "averageRating": 4.0},
    ]
    store = _make_store(mock_db_pool)
    chips = await store.available_filter_chips("user-1")
    assert {"label": "8+", "min": 8.0, "max": 10.0} in chips["ratings"]
    assert {"label": "6\u20138", "min": 6.0, "max": 7.99} in chips["ratings"]
    assert {"label": "<6", "min": 0.0, "max": 5.99} in chips["ratings"]


async def test_available_filter_chips_empty_watched(mock_db_pool):
    """available_filter_chips() returns empty lists when user has no films."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)
    chips = await store.available_filter_chips("user-1")
    assert chips["decades"] == []
    assert chips["genres"] == []
    assert chips["ratings"] == []


async def test_available_filter_chips_skips_null_genres(mock_db_pool):
    """available_filter_chips() ignores rows with None/empty genres."""
    mock_db_pool.execute.return_value = [
        {"startYear": 2024, "genres": None, "averageRating": 7.0},
        {"startYear": 2024, "genres": "", "averageRating": 7.0},
        {"startYear": 2024, "genres": "Drama", "averageRating": 7.0},
    ]
    store = _make_store(mock_db_pool)
    chips = await store.available_filter_chips("user-1")
    assert chips["genres"] == ["Drama"]
