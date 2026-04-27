"""Tests for movies.watchlist_store — WatchlistStore data-access layer."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from movies.watchlist_store import WatchlistStore


def _make_store(mock_db_pool, cache=None) -> WatchlistStore:
    return WatchlistStore(mock_db_pool, cache=cache)


# ── add ──────────────────────────────────────────────────────────────


async def test_add_executes_insert_with_correct_params(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.add("user-1", "tt1234567")

    mock_db_pool.execute.assert_awaited_once()
    args = mock_db_pool.execute.call_args
    query, params = args[0][0], args[0][1]
    assert "INSERT INTO user_watchlist" in query
    assert "ON DUPLICATE KEY UPDATE" in query
    assert params[0] == "user-1"
    assert params[1] == "tt1234567"
    assert args[1]["fetch"] == "none"


async def test_add_is_idempotent(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.add("user-1", "tt1")
    await store.add("user-1", "tt1")

    assert mock_db_pool.execute.await_count == 2


# ── remove ───────────────────────────────────────────────────────────


async def test_remove_executes_delete_with_correct_params(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.remove("user-1", "tt1234567")

    args = mock_db_pool.execute.call_args
    query, params = args[0][0], args[0][1]
    assert "DELETE FROM user_watchlist" in query
    assert params == ["user-1", "tt1234567"]
    assert args[1]["fetch"] == "none"


# ── is_in_watchlist ──────────────────────────────────────────────────


async def test_is_in_watchlist_returns_true_when_row_exists(mock_db_pool):
    mock_db_pool.execute.return_value = {"cnt": 1}
    store = _make_store(mock_db_pool)

    assert await store.is_in_watchlist("user-1", "tt1") is True


async def test_is_in_watchlist_returns_false_when_no_row(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    assert await store.is_in_watchlist("user-1", "tt1") is False


# ── watchlist_tconsts ────────────────────────────────────────────────


async def test_watchlist_tconsts_returns_set_of_tconsts(mock_db_pool):
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}, {"tconst": "tt2"}]
    store = _make_store(mock_db_pool)

    result = await store.watchlist_tconsts("user-1")

    assert result == {"tt1", "tt2"}


async def test_watchlist_tconsts_uses_cache_when_available(mock_db_pool, cache_stub):
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}]
    store = _make_store(mock_db_pool, cache=cache_stub)

    first = await store.watchlist_tconsts("user-1")
    second = await store.watchlist_tconsts("user-1")

    assert first == {"tt1"}
    assert second == {"tt1"}
    # safe_get_or_set hits the loader once, then serves cached.
    assert mock_db_pool.execute.await_count == 1


async def test_watchlist_tconsts_caches_empty_list_for_users_with_no_entries(
    mock_db_pool, cache_stub
):
    """Empty-watchlist users must not re-query MySQL on every navigation.

    safe_get_or_set's contract treats ``[]`` like any other non-None value:
    it stores it, and ``get()`` returns it as a cache *hit* (not a miss).
    A regression here would amplify MySQL load for brand-new accounts —
    the most common shape on this table.
    """
    mock_db_pool.execute.return_value = []  # user has nothing on their watchlist
    store = _make_store(mock_db_pool, cache=cache_stub)

    first = await store.watchlist_tconsts("user-1")
    second = await store.watchlist_tconsts("user-1")
    third = await store.watchlist_tconsts("user-1")

    assert first == set()
    assert second == set()
    assert third == set()
    assert mock_db_pool.execute.await_count == 1, (
        "loader must run only on the cold-cache call, not on every request"
    )


async def test_add_invalidates_cache(mock_db_pool, cache_stub):
    from infra.cache import CacheNamespace

    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool, cache=cache_stub)
    # Pre-warm a cached value so we can detect invalidation.
    await cache_stub.set(CacheNamespace.USER, "watchlist_tconsts:user-1", ["tt-old"])

    await store.add("user-1", "tt-new")

    # After invalidation, get returns None → next read goes to loader.
    cached = await cache_stub.get(CacheNamespace.USER, "watchlist_tconsts:user-1")
    assert cached is None


# ── count ────────────────────────────────────────────────────────────


async def test_count_returns_integer(mock_db_pool):
    mock_db_pool.execute.return_value = {"cnt": 7}
    store = _make_store(mock_db_pool)

    assert await store.count("user-1") == 7


# ── list_watchlist_filtered ──────────────────────────────────────────


async def test_list_watchlist_filtered_default_sort_is_recent(mock_db_pool):
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered("user-1", limit=10, offset=0)

    sql = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.added_at DESC" in sql


async def test_list_watchlist_filtered_supports_title_sort(mock_db_pool):
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered("user-1", sort="title_asc", limit=10, offset=0)

    sql = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY c.primaryTitle ASC" in sql


async def test_list_watchlist_filtered_falls_back_to_recent_for_unknown_sort(mock_db_pool):
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered("user-1", sort="bogus", limit=10, offset=0)

    sql = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.added_at DESC" in sql


async def test_list_watchlist_filtered_selects_public_id(mock_db_pool):
    """The list query must include p.public_id so templates can build URLs."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered("user-123", limit=10, offset=0)

    sql = mock_db_pool.execute.call_args[0][0]
    assert "p.public_id" in sql


async def test_list_watchlist_filtered_applies_decade_filter(mock_db_pool):
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered(
        "user-1", limit=10, offset=0, decades=["1990"]
    )

    sql, params = mock_db_pool.execute.call_args[0][:2]
    assert "c.startYear >= %s" in sql and "c.startYear <= %s" in sql
    assert 1990 in params and 1999 in params


async def test_count_filtered_returns_count(mock_db_pool):
    mock_db_pool.execute.return_value = {"cnt": 4}
    store = _make_store(mock_db_pool)

    assert await store.count_filtered("user-1") == 4


# ── available_filter_chips ───────────────────────────────────────────


async def test_available_filter_chips_returns_decades_genres_ratings(mock_db_pool):
    mock_db_pool.execute.return_value = [
        {"startYear": 1995, "genres": "Drama,Comedy", "averageRating": 8.2},
        {"startYear": 2003, "genres": "Drama", "averageRating": 6.5},
    ]
    store = _make_store(mock_db_pool)

    chips = await store.available_filter_chips("user-1")

    assert "1990s" in chips["decades"]
    assert "2000s" in chips["decades"]
    assert "Drama" in chips["genres"]
    assert "Comedy" in chips["genres"]
    assert any(t["label"] == "8+" for t in chips["ratings"])
    assert any(t["label"] == "6–8" for t in chips["ratings"])
