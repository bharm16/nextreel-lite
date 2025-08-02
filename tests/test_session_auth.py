import asyncio
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from app import create_app


def test_session_auto_initialization():
    async def run_test():
        with patch('app.MovieManager') as MockManager:
            manager = MockManager.return_value
            manager.add_user = AsyncMock()
            manager.movie_queue_manager = SimpleNamespace(start_populate_task=AsyncMock())
            manager.home = AsyncMock(return_value='home')
            app = create_app()
            app.config['TESTING'] = False
            async with app.app_context():
                client = app.test_client()
                response = await client.get('/')
                assert response.status_code == 200
                assert manager.add_user.called
                assert manager.movie_queue_manager.start_populate_task.called
    asyncio.run(run_test())
