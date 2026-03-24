import asyncio
from unittest.mock import AsyncMock, patch

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
async def test_add_user_sets_criteria_and_loads_queue(app):
    async with app.test_request_context("/"):
        movie_manager = MovieManager(db_config=None)
        movie_manager._navigator.load_initial_queue = AsyncMock()
        await movie_manager.add_user("test_user", {"genre": "comedy"})
        assert session["criteria"] == {"genre": "comedy"}
        movie_manager._navigator.load_initial_queue.assert_awaited_once()


@pytest.mark.asyncio
async def test_home_reuses_existing_prefetch_task(app):
    render_template_mock = AsyncMock(return_value="rendered_template")
    movie_manager = MovieManager(db_config=None)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_ensure_queue():
        started.set()
        await release.wait()

    movie_manager._navigator._ensure_queue = fake_ensure_queue

    async with app.test_request_context("/"):
        session[USER_ID_KEY] = "test-user"
        first_result = await movie_manager.home("test-user")
        await started.wait()
        second_result = await movie_manager.home("test-user")

        # home() now returns a data dict, not a rendered template
        assert isinstance(first_result, dict)
        assert "default_backdrop_url" in first_result
        assert isinstance(second_result, dict)
        assert len(movie_manager._queue_prefetch_tasks) == 1

        release.set()
        task = next(iter(movie_manager._queue_prefetch_tasks.values()))
        await task
        await asyncio.sleep(0)
        assert movie_manager._queue_prefetch_tasks == {}


@pytest.mark.asyncio
async def test_close_cancels_prefetch_tasks(app):
    movie_manager = MovieManager(db_config=None)
    movie_manager.tmdb_helper.close = AsyncMock()
    movie_manager.db_pool.close_pool = AsyncMock()
    release = asyncio.Event()

    async def fake_ensure_queue():
        await release.wait()

    movie_manager._navigator._ensure_queue = fake_ensure_queue

    async with app.test_request_context("/"):
        session[USER_ID_KEY] = "test-user"
        await movie_manager.home("test-user")
        task = next(iter(movie_manager._queue_prefetch_tasks.values()))
        assert not task.done()

        await movie_manager.close()

        assert task.cancelled()
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
