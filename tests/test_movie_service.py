import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from infra.navigation_state import NavigationState, default_filter_state, utcnow
from movie_service import MovieManager

_TEST_ENV = {"TMDB_API_KEY": "test-key", "FLASK_SECRET_KEY": "test-secret"}


def _state() -> NavigationState:
    now = utcnow()
    return NavigationState(
        session_id="state-1",
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
    )


@pytest.mark.asyncio
@patch.dict(os.environ, _TEST_ENV)
async def test_start_initializes_pool_schema_and_backdrop():
    movie_manager = MovieManager(db_config=None)
    movie_manager.db_pool.init_pool = AsyncMock()

    with patch("movie_service.ensure_runtime_schema", AsyncMock()) as ensure_schema:
        with patch.object(MovieManager, "set_default_backdrop", AsyncMock()) as set_backdrop:
            await movie_manager.start()

    movie_manager.db_pool.init_pool.assert_awaited_once()
    ensure_schema.assert_awaited_once_with(movie_manager.db_pool)
    set_backdrop.assert_awaited_once()
    assert movie_manager.navigation_state_store is not None
    assert movie_manager._navigator is not None


@pytest.mark.asyncio
@patch.dict(os.environ, _TEST_ENV)
async def test_start_continues_when_tmdb_backdrop_warmup_fails():
    request = httpx.Request("GET", "https://api.themoviedb.org/3/movie/62/images")
    response = httpx.Response(401, request=request)
    backdrop_error = httpx.HTTPStatusError(
        "Client error '401 Unauthorized'",
        request=request,
        response=response,
    )

    movie_manager = MovieManager(db_config=None)
    movie_manager.db_pool.init_pool = AsyncMock()
    movie_manager.tmdb_helper.get_images_by_tmdb_id = AsyncMock(side_effect=backdrop_error)

    with patch("movie_service.ensure_runtime_schema", AsyncMock()):
        await movie_manager.start()

    movie_manager.db_pool.init_pool.assert_awaited_once()
    assert movie_manager.default_backdrop_url is None


@pytest.mark.asyncio
@patch.dict(os.environ, _TEST_ENV)
async def test_home_prewarm_only_runs_for_empty_queue():
    movie_manager = MovieManager(db_config=None)
    movie_manager._navigator = AsyncMock()

    state = _state()
    await movie_manager.home(state)
    movie_manager._navigator.prewarm_queue.assert_awaited_once_with("state-1", legacy_session=None)

    movie_manager._navigator.prewarm_queue.reset_mock()
    state.queue = [{"tconst": "tt1", "title": "Movie", "slug": "movie"}]
    await movie_manager.home(state)
    movie_manager._navigator.prewarm_queue.assert_not_awaited()


@pytest.mark.asyncio
@patch.dict(os.environ, _TEST_ENV)
async def test_filtered_movie_normalizes_and_delegates():
    class FormStub:
        def get(self, key, default=None):
            values = {
                "year_min": "1990",
                "year_max": "2000",
                "language": "fr",
                "imdb_score_min": "6.5",
            }
            return values.get(key, default)

        def getlist(self, key):
            if key == "genres[]":
                return ["Drama", "Comedy"]
            return []

    movie_manager = MovieManager(db_config=None)
    movie_manager._navigator = AsyncMock()
    movie_manager._navigator.apply_filters = AsyncMock(return_value="redirect")
    state = _state()

    result = await movie_manager.filtered_movie(state, FormStub())

    assert result == "redirect"
    movie_manager._navigator.apply_filters.assert_awaited_once()
    _, filters = movie_manager._navigator.apply_filters.await_args.args[:2]
    assert filters["language"] == "fr"
    assert filters["genres_selected"] == ["Drama", "Comedy"]
