"""Tests for movies.watched_store — WatchedStore data-access layer."""

from __future__ import annotations

import pytest

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
