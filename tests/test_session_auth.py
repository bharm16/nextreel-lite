import time
from unittest.mock import AsyncMock

import pytest
from quart import Quart, session

from session_auth import _default_criteria, init_session
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
async def test_init_session_preserves_existing_user(app):
    """Session expiry is now handled by EnhancedSessionSecurity, not init_session.

    init_session should keep an existing user_id intact regardless of age.
    """
    movie_manager = AsyncMock()

    async with app.test_request_context("/"):
        session[USER_ID_KEY] = "existing-user"
        session[CREATED_AT_KEY] = time.time() - (25 * 60 * 60)
        session[CRITERIA_KEY] = _default_criteria()
        session[INITIALIZED_KEY] = True

        await init_session(movie_manager)

        # User should NOT be recreated — session lifetime is managed elsewhere
        assert session[USER_ID_KEY] == "existing-user"
        movie_manager.add_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_init_session_reinitializes_uninitialized_user(app):
    """A user_id without INITIALIZED_KEY should be re-registered in movie manager."""
    movie_manager = AsyncMock()

    async with app.test_request_context("/"):
        session[USER_ID_KEY] = "orphan-user"
        session[CREATED_AT_KEY] = time.time() - (25 * 60 * 60)
        session[CRITERIA_KEY] = _default_criteria()
        # Note: INITIALIZED_KEY is deliberately absent

        await init_session(movie_manager)

        # User id preserved, but re-registered in movie manager
        assert session[USER_ID_KEY] == "orphan-user"
        assert session[INITIALIZED_KEY] is True
        movie_manager.add_user.assert_awaited_once_with(
            "orphan-user", _default_criteria()
        )
