import asyncio
from unittest.mock import AsyncMock, patch

from movie_service import MovieManager


def test_start():
    async def run_test():
        set_default_backdrop_mock = AsyncMock()
        with patch.object(MovieManager, 'set_default_backdrop', set_default_backdrop_mock):
            movie_manager = MovieManager(db_config=None)
            await movie_manager.start()
            set_default_backdrop_mock.assert_called_once()

    asyncio.run(run_test())


def test_add_user():
    async def run_test():
        add_user_mock = AsyncMock()
        movie_manager = MovieManager(db_config=None)
        movie_manager.movie_queue_manager = AsyncMock(add_user=add_user_mock)
        await movie_manager.add_user('test_user', {'genre': 'comedy'})
        add_user_mock.assert_called_once_with('test_user', {'genre': 'comedy'})

    asyncio.run(run_test())


def test_home():
    async def run_test():
        is_task_running_mock = lambda: False
        populate_mock = AsyncMock()
        render_template_mock = AsyncMock(return_value='rendered_template')
        movie_manager = MovieManager(db_config=None)
        movie_manager.movie_queue_manager = AsyncMock(
            is_task_running=is_task_running_mock,
            populate=populate_mock
        )
        orig_create_task = asyncio.create_task
        with patch('movie_service.asyncio.create_task', lambda coro: orig_create_task(coro)):
            with patch('movie_service.render_template', render_template_mock):
                result = await movie_manager.home('test_user')
                await asyncio.sleep(0)  # allow populate task to run
                populate_mock.assert_called_once_with('test_user')
                render_template_mock.assert_called_once_with(
                    'home.html', default_backdrop_url=movie_manager.default_backdrop_url
                )
                assert result == 'rendered_template'

    asyncio.run(run_test())


def test_set_default_backdrop():
    async def run_test():
        class Helper:
            async def get_images_by_tmdb_id(self, tmdb_id):
                return {'backdrops': ['backdrop_url']}

            def get_full_image_url(self, path):
                return f'full:{path}'

        movie_manager = MovieManager(db_config=None)
        movie_manager.tmdb_helper = Helper()
        await movie_manager.set_default_backdrop()
        assert movie_manager.default_backdrop_url == 'full:backdrop_url'

    asyncio.run(run_test())
