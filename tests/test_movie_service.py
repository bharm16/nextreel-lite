import asyncio
from unittest.mock import AsyncMock, patch

import os
from quart import session
from app import create_app
from movie_service import MovieManager

# Provide a dummy TMDb API key for tests to avoid external dependencies
os.environ.setdefault("TMDB_API_KEY", "test_key")


def test_start():
    async def run_test():
        set_default_backdrop_mock = AsyncMock()
        with patch.object(MovieManager, 'set_default_backdrop', set_default_backdrop_mock):
            movie_manager = MovieManager(db_config=None)
            movie_manager.db_pool.init_pool = AsyncMock()
            await movie_manager.start()
            set_default_backdrop_mock.assert_called_once()

    asyncio.run(run_test())


def test_add_user():
    async def run_test():
        app = create_app()
        app.config['TESTING'] = True
        async with app.test_request_context('/'):
            movie_manager = MovieManager(db_config=None)
            with patch.object(MovieManager, '_load_movies_into_queue', AsyncMock()):
                await movie_manager.add_user('test_user', {'genre': 'comedy'})
                assert session['criteria'] == {'genre': 'comedy'}

    asyncio.run(run_test())


def test_home():
    async def run_test():
        render_template_mock = AsyncMock(return_value='rendered_template')
        movie_manager = MovieManager(db_config=None)
        movie_manager._ensure_queue = AsyncMock()
        with patch('movie_service.render_template', render_template_mock):
            result = await movie_manager.home('test_user')
            movie_manager._ensure_queue.assert_awaited_once()
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
