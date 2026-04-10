"""Tests for watched-movie exclusion in queue refill logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from movie_navigator import MovieNavigator
from movies.candidate_store import CandidateStore
from movies.watched_store import WatchedStore


def _make_state(*, user_id="user-1", exclude_watched=True):
    state = MagicMock()
    state.user_id = user_id
    state.filters = {"exclude_watched": exclude_watched}
    state.queue = []
    state.prev = []
    state.future = []
    state.seen = []
    state.current_tconst = None
    return state


async def test_refill_queue_merges_watched_tconsts():
    """Watched tconsts are merged into excluded set when user_id present and exclude_watched=True."""
    mock_db_pool = MagicMock()
    candidate_store = CandidateStore(mock_db_pool)
    watched_store = WatchedStore(mock_db_pool)

    watched_store.watched_tconsts = AsyncMock(return_value={"tt0000001", "tt0000002"})

    captured_excluded = []

    async def fake_fetch(filters, excluded, count):
        captured_excluded.append(set(excluded))
        return []

    candidate_store.fetch_candidate_refs = fake_fetch

    navigator = MovieNavigator(
        candidate_store,
        MagicMock(),
        watched_store=watched_store,
    )

    state = _make_state(user_id="user-1", exclude_watched=True)
    await navigator._refill_queue(state, 5)

    watched_store.watched_tconsts.assert_awaited_once_with("user-1")
    assert len(captured_excluded) == 1
    assert "tt0000001" in captured_excluded[0]
    assert "tt0000002" in captured_excluded[0]


async def test_refill_queue_skips_watched_when_exclude_off():
    """Watched tconsts are NOT excluded when exclude_watched=False."""
    mock_db_pool = MagicMock()
    candidate_store = CandidateStore(mock_db_pool)
    watched_store = WatchedStore(mock_db_pool)

    watched_store.watched_tconsts = AsyncMock(return_value={"tt0000001"})

    captured_excluded = []

    async def fake_fetch(filters, excluded, count):
        captured_excluded.append(set(excluded))
        return []

    candidate_store.fetch_candidate_refs = fake_fetch

    navigator = MovieNavigator(
        candidate_store,
        MagicMock(),
        watched_store=watched_store,
    )

    state = _make_state(user_id="user-1", exclude_watched=False)
    await navigator._refill_queue(state, 5)

    watched_store.watched_tconsts.assert_not_awaited()
    assert len(captured_excluded) == 1
    assert "tt0000001" not in captured_excluded[0]


async def test_refill_queue_no_user_id_skips_watched():
    """watched_tconsts is not called when there is no user_id."""
    mock_db_pool = MagicMock()
    candidate_store = CandidateStore(mock_db_pool)
    watched_store = WatchedStore(mock_db_pool)

    watched_store.watched_tconsts = AsyncMock(return_value={"tt0000001"})

    async def fake_fetch(filters, excluded, count):
        return []

    candidate_store.fetch_candidate_refs = fake_fetch

    navigator = MovieNavigator(
        candidate_store,
        MagicMock(),
        watched_store=watched_store,
    )

    state = _make_state(user_id=None, exclude_watched=True)
    await navigator._refill_queue(state, 5)

    watched_store.watched_tconsts.assert_not_awaited()
