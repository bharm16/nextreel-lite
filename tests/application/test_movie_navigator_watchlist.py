"""Navigator tests for watchlist exclusion behavior."""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nextreel.application.movie_navigator import MovieNavigator


def _state(*, user_id="u1", filters=None, queue=None):
    return SimpleNamespace(
        user_id=user_id,
        filters=filters or {"exclude_watched": True, "exclude_watchlist": True},
        queue=list(queue or []),
        prev=[],
        future=[],
        seen=[],
        current_tconst=None,
        current_ref=None,
    )


async def test_watchlist_exclusion_set_returns_empty_when_no_store():
    nav = MovieNavigator(MagicMock(), MagicMock())
    result = await nav._watchlist_exclusion_set(_state())
    assert result == set()


async def test_watchlist_exclusion_set_returns_empty_when_filter_disabled():
    watchlist_store = MagicMock()
    watchlist_store.watchlist_tconsts = AsyncMock(return_value={"tt1"})
    nav = MovieNavigator(MagicMock(), MagicMock(), watchlist_store=watchlist_store)
    state = _state(filters={"exclude_watched": True, "exclude_watchlist": False})

    result = await nav._watchlist_exclusion_set(state)

    assert result == set()
    watchlist_store.watchlist_tconsts.assert_not_awaited()


async def test_watchlist_exclusion_set_returns_tconsts_when_enabled():
    watchlist_store = MagicMock()
    watchlist_store.watchlist_tconsts = AsyncMock(return_value={"tt1", "tt2"})
    nav = MovieNavigator(MagicMock(), MagicMock(), watchlist_store=watchlist_store)

    result = await nav._watchlist_exclusion_set(_state())

    assert result == {"tt1", "tt2"}


async def test_refill_queue_excludes_watched_and_watchlist_union():
    candidate_store = MagicMock()
    candidate_store.fetch_candidate_refs = AsyncMock(return_value=[])
    watched_store = MagicMock()
    watched_store.watched_tconsts = AsyncMock(return_value={"tt-watched"})
    watchlist_store = MagicMock()
    watchlist_store.watchlist_tconsts = AsyncMock(return_value={"tt-saved"})
    nav = MovieNavigator(
        candidate_store,
        MagicMock(),
        watched_store=watched_store,
        watchlist_store=watchlist_store,
    )

    state = _state()
    await nav._refill_queue(state, desired_size=10)

    excluded = candidate_store.fetch_candidate_refs.await_args[0][1]
    assert "tt-watched" in excluded
    assert "tt-saved" in excluded
