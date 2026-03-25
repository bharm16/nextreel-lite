from unittest.mock import AsyncMock, patch

import httpx
import pytest
from quart import Quart, session

from movie_service import MovieManager
from session.keys import USER_ID_KEY


@pytest.fixture
def app():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    return app


@pytest.mark.asyncio
async def test_start_initializes_pool_and_backdrop():
    set_default_backdrop_mock = AsyncMock()
    with patch.object(MovieManager, "set_default_backdrop", set_default_backdrop_mock):
        movie_manager = MovieManager(db_config=None)
        movie_manager.db_pool.init_pool = AsyncMock()
        await movie_manager.start()
        movie_manager.db_pool.init_pool.assert_awaited_once()
        set_default_backdrop_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_continues_when_tmdb_backdrop_warmup_fails():
    request = httpx.Request("GET", "https://api.themoviedb.org/3/movie/62/images")
    response = httpx.Response(401, request=request)
    backdrop_error = httpx.HTTPStatusError(
        "Client error '401 Unauthorized' for url 'https://api.themoviedb.org/3/movie/62/images'",
        request=request,
        response=response,
    )

    movie_manager = MovieManager(db_config=None)
    movie_manager.db_pool.init_pool = AsyncMock()
    movie_manager.tmdb_helper.get_images_by_tmdb_id = AsyncMock(side_effect=backdrop_error)

    await movie_manager.start()

    movie_manager.db_pool.init_pool.assert_awaited_once()
    movie_manager.tmdb_helper.get_images_by_tmdb_id.assert_awaited_once_with(
        movie_manager.default_movie_tmdb_id
    )
    assert movie_manager.default_backdrop_url is None


@pytest.mark.asyncio
async def test_add_user_sets_criteria_and_loads_queue(app):
    async with app.test_request_context("/"):
        session[USER_ID_KEY] = "test_user"
        movie_manager = MovieManager(db_config=None)
        movie_manager._navigator.load_initial_queue = AsyncMock()
        await movie_manager.add_user("test_user", {"genre": "comedy"})
        assert session["criteria"] == {"genre": "comedy"}
        movie_manager._navigator.load_initial_queue.assert_awaited_once()


@pytest.mark.asyncio
async def test_home_returns_default_backdrop_data(app):
    movie_manager = MovieManager(db_config=None)

    async with app.test_request_context("/"):
        session[USER_ID_KEY] = "test-user"
        first_result = await movie_manager.home("test-user")
        second_result = await movie_manager.home("test-user")

        assert isinstance(first_result, dict)
        assert "default_backdrop_url" in first_result
        assert isinstance(second_result, dict)


@pytest.mark.asyncio
async def test_close_closes_tmdb_and_database_resources(app):
    movie_manager = MovieManager(db_config=None)
    movie_manager.tmdb_helper.close = AsyncMock()
    movie_manager.db_pool.close_pool = AsyncMock()

    async with app.test_request_context("/"):
        await movie_manager.close()

    movie_manager.tmdb_helper.close.assert_awaited_once()
    movie_manager.db_pool.close_pool.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_default_backdrop():
    class Helper:
        async def get_images_by_tmdb_id(self, tmdb_id):
            return {"backdrops": ["backdrop_url"]}

        def get_full_image_url(self, path):
            return f"full:{path}"

    movie_manager = MovieManager(db_config=None)
    movie_manager.tmdb_helper = Helper()
    await movie_manager.set_default_backdrop()
    assert movie_manager.default_backdrop_url == "full:backdrop_url"
