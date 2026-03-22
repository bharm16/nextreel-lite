import time
from unittest.mock import AsyncMock

import pytest
from quart import Quart, session

from session_auth import DEFAULT_CRITERIA, init_session
from session_keys import CREATED_AT_KEY, CRITERIA_KEY, INITIALIZED_KEY, USER_ID_KEY


@pytest.fixture
def app():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    return app


@pytest.mark.asyncio
async def test_init_session_creates_user_and_initializes_manager(app):
    movie_manager = AsyncMock()

    async with app.test_request_context("/"):
        await init_session(movie_manager)

        assert USER_ID_KEY in session
        assert CREATED_AT_KEY in session
        assert session[INITIALIZED_KEY] is True
        movie_manager.add_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_session_uses_existing_criteria(app):
    movie_manager = AsyncMock()

    async with app.test_request_context("/"):
        session[USER_ID_KEY] = "existing-user"
        session[CREATED_AT_KEY] = time.time()
        session[CRITERIA_KEY] = {"language": "fr", "genres": ["Drama"]}

        await init_session(movie_manager)

        movie_manager.add_user.assert_awaited_once_with(
            "existing-user", {"language": "fr", "genres": ["Drama"]}
        )


@pytest.mark.asyncio
async def test_init_session_recreates_expired_session(app):
    movie_manager = AsyncMock()

    async with app.test_request_context("/"):
        session[USER_ID_KEY] = "expired-user"
        session[CREATED_AT_KEY] = time.time() - (25 * 60 * 60)
        session[CRITERIA_KEY] = DEFAULT_CRITERIA

        await init_session(movie_manager)

        assert session[USER_ID_KEY] != "expired-user"
        assert session[INITIALIZED_KEY] is True
        assert movie_manager.add_user.await_count == 1
