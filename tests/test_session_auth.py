import asyncio
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from quart import session

from app import create_app
from session_auth import (
    SESSION_FINGERPRINT_KEY,
    SESSION_TOKEN_KEY,
    ensure_session,
    generate_fingerprint,
)


def test_generate_fingerprint_is_deterministic():
    fp1 = generate_fingerprint("agent", "127.0.0.1")
    fp2 = generate_fingerprint("agent", "127.0.0.1")
    assert fp1 == fp2


def test_ensure_session_adds_keys():
    async def run():
        app = create_app()
        async with app.test_request_context("/", headers={"User-Agent": "agent"}):
            ensure_session()
            assert SESSION_TOKEN_KEY in session
            assert SESSION_FINGERPRINT_KEY in session

    asyncio.run(run())


def test_session_auto_initialization():
    async def run_test():
        with patch("app.MovieManager") as MockManager:
            manager = MockManager.return_value
            manager.add_user = AsyncMock()
            manager.movie_queue_manager = SimpleNamespace(
                start_populate_task=AsyncMock()
            )
            manager.home = AsyncMock(return_value="home")
            app = create_app()
            app.config["TESTING"] = False
            async with app.app_context():
                client = app.test_client()
                response = await client.get("/")
                assert response.status_code == 200
                assert manager.add_user.called
                assert manager.movie_queue_manager.start_populate_task.called

    asyncio.run(run_test())
