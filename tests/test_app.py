import asyncio
from unittest.mock import AsyncMock, patch

from app import create_app

def test_home():
    """Ensure the home route returns HTTP 200."""

    async def run_test():
        with patch('app.MovieManager') as MockManager:
            manager = MockManager.return_value
            manager.home = AsyncMock(return_value='home')

            app = create_app()
            app.config['TESTING'] = True
            async with app.app_context():
                client = app.test_client()
                response = await client.get("/")
                assert response.status_code == 200

    asyncio.run(run_test())


def test_set_filters_route():
    async def run_test():
        with patch('app.MovieManager') as MockManager:
            MockManager.return_value.start = AsyncMock()
            app = create_app()
            app.config['TESTING'] = True
            async with app.app_context():
                client = app.test_client()
                response = await client.get('/setFilters')
                assert response.status_code == 200

    asyncio.run(run_test())


def test_filtered_movie_endpoint():
    async def run_test():
        with patch('app.MovieManager') as MockManager:
            manager = MockManager.return_value
            manager.filtered_movie = AsyncMock(return_value='filtered')

            app = create_app()
            app.config['TESTING'] = True
            async with app.app_context():
                client = app.test_client()
                response = await client.post('/filtered_movie', data={'year_min': '2000'})
                assert response.status_code == 200

    asyncio.run(run_test())


def test_movie_detail_route():
    async def run_test():
        with patch('app.MovieManager') as MockManager:
            manager = MockManager.return_value
            manager.render_movie_by_tconst = AsyncMock(return_value='detail')

            app = create_app()
            app.config['TESTING'] = True
            async with app.app_context():
                client = app.test_client()
                response = await client.get('/movie/tt1234567')
                assert response.status_code == 200

    asyncio.run(run_test())


def test_next_previous_movie_routes():
    async def run_test():
        with patch('app.MovieManager') as MockManager:
            manager = MockManager.return_value
            manager.next_movie = AsyncMock(return_value='next')
            manager.previous_movie = AsyncMock(return_value='prev')

            app = create_app()
            app.config['TESTING'] = True
            async with app.app_context():
                client = app.test_client()
                response = await client.post('/next_movie')
                assert response.status_code == 200
                response = await client.post('/previous_movie')
                assert response.status_code == 200

    asyncio.run(run_test())


def test_handle_new_user_route():
    async def run_test():
        with patch('app.MovieManager') as MockManager:
            manager = MockManager.return_value
            manager.movie_queue_manager = AsyncMock(add_user=AsyncMock())

            app = create_app()
            app.config['TESTING'] = True
            async with app.app_context():
                client = app.test_client()
                response = await client.get('/handle_new_user')
                assert response.status_code == 302

    asyncio.run(run_test())

