"""Route-level characterization tests for register and Google OAuth flows."""

from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import g, session

import routes
from session.keys import SESSION_OAUTH_STATE_KEY
from tests.helpers import TEST_ENV


@contextmanager
def _make_auth_app(extra_env: dict[str, str] | None = None):
    env = {**TEST_ENV, **(extra_env or {})}
    with patch.dict(os.environ, env, clear=False), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        app.navigation_state_store = AsyncMock()
        app.navigation_state_store.set_user_id = AsyncMock()
        yield app, manager


def _nav_state(*, user_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        csrf_token="test-csrf-token",
        session_id="test-session-id",
        user_id=user_id,
        filters={},
    )


class _FakeAsyncClient:
    def __init__(self, *, post_response=None, get_response=None):
        self.post = AsyncMock(return_value=post_response)
        self.get = AsyncMock(return_value=get_response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestRegisterRoute:
    @pytest.mark.asyncio
    async def test_register_validation_errors_preserve_current_messages(self):
        with _make_auth_app() as (app, _manager):
            async with app.test_request_context(
                "/register",
                method="POST",
                form={
                    "email": "not-an-email",
                    "password": "short",
                    "confirm_password": "different",
                },
                headers={"X-CSRFToken": "test-csrf-token"},
            ):
                g.navigation_state = _nav_state()

                body, status_code = await routes.register_submit()

        assert status_code == 400
        assert "Please enter a valid email address." in body
        assert "Password must be at least 8 characters." in body
        assert "Passwords do not match." in body

    @pytest.mark.asyncio
    async def test_register_duplicate_email_precheck_preserves_response(self):
        with _make_auth_app() as (app, _manager):
            with patch(
                "session.user_auth.get_user_by_email",
                AsyncMock(return_value={"user_id": "existing-user"}),
            ), patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")), patch(
                "session.user_auth.register_user", AsyncMock()
            ) as register_user:
                async with app.test_request_context(
                    "/register",
                    method="POST",
                    form={
                        "email": "person@example.com",
                        "password": "password123",
                        "confirm_password": "password123",
                    },
                    headers={"X-CSRFToken": "test-csrf-token"},
                ):
                    g.navigation_state = _nav_state()

                    body, status_code = await routes.register_submit()

        assert status_code == 400
        assert "An account with this email already exists." in body
        register_user.assert_not_awaited()
        app.navigation_state_store.set_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_register_duplicate_race_preserves_response(self):
        with _make_auth_app() as (app, _manager):
            from session.user_auth import DuplicateUserError

            with patch(
                "session.user_auth.get_user_by_email",
                AsyncMock(return_value=None),
            ), patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")), patch(
                "session.user_auth.register_user",
                AsyncMock(side_effect=DuplicateUserError("person@example.com")),
            ):
                async with app.test_request_context(
                    "/register",
                    method="POST",
                    form={
                        "email": "person@example.com",
                        "password": "password123",
                        "confirm_password": "password123",
                    },
                    headers={"X-CSRFToken": "test-csrf-token"},
                ):
                    g.navigation_state = _nav_state()

                    body, status_code = await routes.register_submit()

        assert status_code == 400
        assert "An account with this email already exists." in body
        app.navigation_state_store.set_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_register_success_binds_session_and_redirects_home(self):
        with _make_auth_app() as (app, _manager):
            with patch(
                "session.user_auth.get_user_by_email",
                AsyncMock(return_value=None),
            ), patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")), patch(
                "session.user_auth.register_user", AsyncMock(return_value="user-123")
            ):
                async with app.test_request_context(
                    "/register",
                    method="POST",
                    form={
                        "email": "person@example.com",
                        "password": "password123",
                        "confirm_password": "password123",
                        "display_name": "Pat",
                    },
                    headers={"X-CSRFToken": "test-csrf-token"},
                ):
                    g.navigation_state = _nav_state()

                    response = await routes.register_submit()

        assert response.status_code == 303
        assert response.location.endswith("/")
        app.navigation_state_store.set_user_id.assert_awaited_once_with(
            "test-session-id", "user-123"
        )


class TestGoogleOAuthCallback:
    @pytest.mark.asyncio
    async def test_google_callback_invalid_state_flashes_error_and_redirects(self):
        with _make_auth_app(
            {
                "GOOGLE_CLIENT_ID": "google-client-id",
                "GOOGLE_CLIENT_SECRET": "google-secret",
            }
        ) as (app, _manager):
            with patch("routes.flash", AsyncMock()) as flash_mock:
                async with app.test_request_context(
                    "/auth/google/callback?state=wrong&code=abc",
                    method="GET",
                ):
                    g.navigation_state = _nav_state()
                    session[SESSION_OAUTH_STATE_KEY] = "expected"

                    response = await routes.auth_google_callback()

        assert response.status_code == 302
        assert response.location.endswith("/login")
        flash_mock.assert_awaited_once_with(
            "Google sign-in failed. Please try again.",
            "error",
        )

    @pytest.mark.asyncio
    async def test_google_callback_provider_conflict_preserves_flash_contract(self):
        token_response = MagicMock(status_code=200)
        token_response.json.return_value = {"access_token": "oauth-token"}
        userinfo_response = MagicMock(status_code=200)
        userinfo_response.json.return_value = {
            "email": "person@example.com",
            "sub": "google-subject",
            "name": "Pat Example",
        }

        with _make_auth_app(
            {
                "GOOGLE_CLIENT_ID": "google-client-id",
                "GOOGLE_CLIENT_SECRET": "google-secret",
            }
        ) as (app, _manager):
            with patch(
                "auth_flows.httpx.AsyncClient",
                return_value=_FakeAsyncClient(
                    post_response=token_response,
                    get_response=userinfo_response,
                ),
            ), patch(
                "session.user_auth.get_user_by_email",
                AsyncMock(
                    return_value={
                        "user_id": "existing-user",
                        "auth_provider": "email",
                    }
                ),
            ), patch(
                "session.user_auth.find_or_create_oauth_user", AsyncMock()
            ) as create_oauth_user, patch(
                "routes.flash", AsyncMock()
            ) as flash_mock:
                async with app.test_request_context(
                    "/auth/google/callback?state=expected&code=abc",
                    method="GET",
                ):
                    g.navigation_state = _nav_state()
                    session[SESSION_OAUTH_STATE_KEY] = "expected"

                    response = await routes.auth_google_callback()

        assert response.status_code == 302
        assert response.location.endswith("/login")
        flash_mock.assert_awaited_once_with(
            "An account with this email already exists. Please log in with email.",
            "error",
        )
        create_oauth_user.assert_not_awaited()
        app.navigation_state_store.set_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_google_callback_success_binds_session_and_redirects(self):
        token_response = MagicMock(status_code=200)
        token_response.json.return_value = {"access_token": "oauth-token"}
        userinfo_response = MagicMock(status_code=200)
        userinfo_response.json.return_value = {
            "email": "person@example.com",
            "sub": "google-subject",
            "name": "Pat Example",
        }

        with _make_auth_app(
            {
                "GOOGLE_CLIENT_ID": "google-client-id",
                "GOOGLE_CLIENT_SECRET": "google-secret",
            }
        ) as (app, _manager):
            with patch(
                "auth_flows.httpx.AsyncClient",
                return_value=_FakeAsyncClient(
                    post_response=token_response,
                    get_response=userinfo_response,
                ),
            ), patch(
                "session.user_auth.get_user_by_email",
                AsyncMock(return_value=None),
            ), patch(
                "session.user_auth.find_or_create_oauth_user",
                AsyncMock(return_value="oauth-user-123"),
            ) as create_oauth_user:
                async with app.test_request_context(
                    "/auth/google/callback?state=expected&code=abc",
                    method="GET",
                ):
                    g.navigation_state = _nav_state()
                    session[SESSION_OAUTH_STATE_KEY] = "expected"

                    response = await routes.auth_google_callback()

        assert response.status_code == 303
        assert response.location.endswith("/")
        create_oauth_user.assert_awaited_once()
        app.navigation_state_store.set_user_id.assert_awaited_once_with(
            "test-session-id",
            "oauth-user-123",
        )
