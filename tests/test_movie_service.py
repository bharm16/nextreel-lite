import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from infra.navigation_state import NavigationState, default_filter_state
from infra.time_utils import utcnow
from movie_navigator import NavigationOutcome
from tests.helpers import TEST_ENV
from movie_service import MovieManager


def _state() -> NavigationState:
    now = utcnow()
    return NavigationState(
        session_id="state-1",
        version=1,
        csrf_token="csrf",
        filters=default_filter_state(),
        current_tconst=None,
        current_ref=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
    )


@pytest.mark.asyncio
@patch.dict(os.environ, TEST_ENV)
async def test_start_initializes_pool_schema_without_backdrop_warmup():
    movie_manager = MovieManager(db_config=None)
    movie_manager.db_pool.init_pool = AsyncMock()

    with patch("movie_service.ensure_runtime_schema", AsyncMock()) as ensure_schema:
        with patch.object(MovieManager, "set_default_backdrop", AsyncMock()) as set_backdrop:
            await movie_manager.start()

    movie_manager.db_pool.init_pool.assert_awaited_once()
    ensure_schema.assert_awaited_once_with(movie_manager.db_pool)
    set_backdrop.assert_not_awaited()
    assert movie_manager.navigation_state_store is not None
    assert movie_manager._navigator is not None


@pytest.mark.asyncio
@patch.dict(os.environ, TEST_ENV)
async def test_start_leaves_default_backdrop_unset():
    movie_manager = MovieManager(db_config=None)
    movie_manager.db_pool.init_pool = AsyncMock()

    with patch("movie_service.ensure_runtime_schema", AsyncMock()):
        await movie_manager.start()

    movie_manager.db_pool.init_pool.assert_awaited_once()
    assert movie_manager.default_backdrop_url is None


@pytest.mark.asyncio
@patch.dict(os.environ, TEST_ENV)
async def test_home_prewarm_only_runs_for_empty_queue():
    movie_manager = MovieManager(db_config=None)
    movie_manager._navigator = AsyncMock()

    state = _state()
    await movie_manager.home(state)
    movie_manager._navigator.prewarm_queue.assert_awaited_once_with(
        "state-1",
        legacy_session=None,
        current_state=state,
    )

    movie_manager._navigator.prewarm_queue.reset_mock()
    state.queue = [{"tconst": "tt1", "title": "Movie", "slug": "movie"}]
    await movie_manager.home(state)
    movie_manager._navigator.prewarm_queue.assert_not_awaited()


@pytest.mark.asyncio
@patch.dict(os.environ, TEST_ENV)
async def test_filtered_movie_normalizes_and_delegates():
    movie_manager = MovieManager(db_config=None)
    movie_manager._navigator = AsyncMock()
    movie_manager._navigator.apply_filters = AsyncMock(
        return_value=NavigationOutcome(tconst="tt1234567")
    )
    state = _state()
    filters = {
        "year_min": "1990",
        "year_max": "2000",
        "language": "fr",
        "imdb_score_min": "6.5",
        "genres_selected": ["Drama", "Comedy"],
    }

    result = await movie_manager.filtered_movie(state, filters)

    assert result == NavigationOutcome(tconst="tt1234567")
    movie_manager._navigator.apply_filters.assert_awaited_once()
    _, delegated_filters = movie_manager._navigator.apply_filters.await_args.args[:2]
    assert delegated_filters == filters


@pytest.mark.asyncio
@patch.dict(os.environ, {**TEST_ENV, "PREWARM_TIMEOUT_SECONDS": "0.05"})
async def test_home_prewarm_respects_timeout():
    """A hanging prewarm_queue must NOT block home() beyond the timeout."""
    movie_manager = MovieManager(db_config=None)
    movie_manager._navigator = AsyncMock()

    async def hang(*args, **kwargs):
        await asyncio.sleep(5.0)

    movie_manager._navigator.prewarm_queue = AsyncMock(side_effect=hang)

    state = _state()
    loop = asyncio.get_event_loop()
    start = loop.time()
    result = await movie_manager.home(state)
    elapsed = loop.time() - start

    assert elapsed < 1.0, f"home() took {elapsed}s -- prewarm not bounded"
    assert result == {"default_backdrop_url": None}
