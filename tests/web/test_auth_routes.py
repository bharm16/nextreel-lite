"""Route-level characterization tests for register and Google OAuth flows."""

from __future__ import annotations

import builtins
import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import g, session
from werkzeug.exceptions import Conflict

import nextreel.web.routes as routes
from session.keys import SESSION_OAUTH_STATE_KEY
from tests.helpers import TEST_ENV


@contextmanager
def _make_auth_app(extra_env: dict[str, str] | None = None):
    env = {**TEST_ENV, **(extra_env or {})}
    with patch.dict(os.environ, env, clear=False), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool.execute = AsyncMock(return_value=None)

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        app.navigation_state_store = AsyncMock()
        app.navigation_state_store.set_user_id = AsyncMock()
        app.navigation_state_store.bind_user = AsyncMock()
        yield app, manager


@contextmanager
def _missing_bcrypt_import():
    real_import = builtins.__import__
    original_bcrypt = sys.modules.pop("bcrypt", None)
    original_user_auth = sys.modules.pop("session.user_auth", None)

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "bcrypt":
            exc = ModuleNotFoundError("No module named 'bcrypt'")
            exc.name = "bcrypt"
            raise exc
        return real_import(name, globals, locals, fromlist, level)

    try:
        with patch("builtins.__import__", side_effect=_import):
            yield
    finally:
        sys.modules.pop("bcrypt", None)
        sys.modules.pop("session.user_auth", None)
        if original_bcrypt is not None:
            sys.modules["bcrypt"] = original_bcrypt
        if original_user_auth is not None:
            sys.modules["session.user_auth"] = original_user_auth


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
            with (
                patch(
                    "session.user_auth.get_user_by_email",
                    AsyncMock(return_value={"user_id": "existing-user"}),
                ),
                patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")),
                patch("session.user_auth.register_user", AsyncMock()) as register_user,
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
        register_user.assert_not_awaited()
        app.navigation_state_store.set_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_register_duplicate_race_preserves_response(self):
        with _make_auth_app() as (app, _manager):
            from session.user_auth import DuplicateUserError

            with (
                patch(
                    "session.user_auth.get_user_by_email",
                    AsyncMock(return_value=None),
                ),
                patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")),
                patch(
                    "session.user_auth.register_user",
                    AsyncMock(side_effect=DuplicateUserError("person@example.com")),
                ),
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
            bound_state = _nav_state(user_id="user-123")
            bound_state.filters = {"exclude_watched": False}
            app.navigation_state_store.bind_user.return_value = bound_state
            with (
                patch(
                    "session.user_auth.get_user_by_email",
                    AsyncMock(return_value=None),
                ),
                patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")),
                patch("session.user_auth.register_user", AsyncMock(return_value="user-123")),
                patch(
                    "session.user_preferences.get_exclude_watched_default",
                    AsyncMock(return_value=False),
                ) as get_exclude_watched_default,
                patch(
                    "session.user_preferences.get_exclude_watchlist_default",
                    AsyncMock(return_value=True),
                ),
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
                    initial_state = _nav_state()
                    g.navigation_state = initial_state

                    response = await routes.register_submit()
                    attached_state = g.navigation_state

        assert response.status_code == 303
        assert response.location.endswith("/")
        get_exclude_watched_default.assert_awaited_once_with(
            _manager.db_pool,
            "user-123",
        )
        app.navigation_state_store.bind_user.assert_awaited_once_with(
            initial_state,
            "user-123",
            exclude_watched=False,
            exclude_watchlist=True,
        )
        app.navigation_state_store.set_user_id.assert_not_awaited()
        assert attached_state is bound_state

    @pytest.mark.asyncio
    async def test_register_missing_bcrypt_returns_service_unavailable(self):
        with _make_auth_app() as (app, _manager):
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

                with _missing_bcrypt_import():
                    body, status_code = await routes.register_submit()

        assert status_code == 503
        assert "Email/password sign-in is currently unavailable. Please try again later." in body
        app.navigation_state_store.set_user_id.assert_not_awaited()


class TestLoginRoute:
    @pytest.mark.asyncio
    async def test_login_success_loads_preference_and_binds_session(self):
        with _make_auth_app() as (app, _manager):
            bound_state = _nav_state(user_id="user-123")
            bound_state.filters = {"exclude_watched": True}
            app.navigation_state_store.bind_user.return_value = bound_state
            with (
                patch(
                    "session.user_auth.authenticate_user",
                    AsyncMock(return_value="user-123"),
                ),
                patch(
                    "session.user_preferences.get_exclude_watched_default",
                    AsyncMock(return_value=True),
                ) as get_exclude_watched_default,
                patch(
                    "session.user_preferences.get_exclude_watchlist_default",
                    AsyncMock(return_value=True),
                ),
            ):
                async with app.test_request_context(
                    "/login",
                    method="POST",
                    form={
                        "email": "person@example.com",
                        "password": "password123",
                    },
                    headers={"X-CSRFToken": "test-csrf-token"},
                ):
                    initial_state = _nav_state()
                    g.navigation_state = initial_state

                    response = await routes.login_submit()
                    attached_state = g.navigation_state

        assert response.status_code == 303
        assert response.location.endswith("/")
        get_exclude_watched_default.assert_awaited_once_with(
            _manager.db_pool,
            "user-123",
        )
        app.navigation_state_store.bind_user.assert_awaited_once_with(
            initial_state,
            "user-123",
            exclude_watched=True,
            exclude_watchlist=True,
        )
        app.navigation_state_store.set_user_id.assert_not_awaited()
        assert attached_state is bound_state

    @pytest.mark.asyncio
    async def test_login_bind_conflict_returns_409(self):
        with _make_auth_app() as (app, _manager):
            app.navigation_state_store.bind_user.return_value = None
            with (
                patch(
                    "session.user_auth.authenticate_user",
                    AsyncMock(return_value="user-123"),
                ),
                patch(
                    "session.user_preferences.get_exclude_watched_default",
                    AsyncMock(return_value=True),
                ),
            ):
                async with app.test_request_context(
                    "/login",
                    method="POST",
                    form={
                        "email": "person@example.com",
                        "password": "password123",
                    },
                    headers={"X-CSRFToken": "test-csrf-token"},
                ):
                    g.navigation_state = _nav_state()

                    with pytest.raises(Conflict) as exc_info:
                        await routes.login_submit()

        assert exc_info.value.code == 409
        assert exc_info.value.description == (
            "Could not bind authenticated user to navigation state"
        )
        app.navigation_state_store.set_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_login_missing_bcrypt_returns_service_unavailable(self):
        with _make_auth_app() as (app, _manager):
            _manager.db_pool.execute.return_value = {
                "user_id": "user-123",
                "password_hash": "stored-hash",
            }
            async with app.test_request_context(
                "/login",
                method="POST",
                form={
                    "email": "person@example.com",
                    "password": "password123",
                },
                headers={"X-CSRFToken": "test-csrf-token"},
            ):
                g.navigation_state = _nav_state()

                with _missing_bcrypt_import():
                    body, status_code = await routes.login_submit()

        assert status_code == 503
        assert "Email/password sign-in is currently unavailable. Please try again later." in body
        app.navigation_state_store.set_user_id.assert_not_awaited()


class TestGoogleOAuthCallback:
    @pytest.mark.asyncio
    async def test_google_callback_invalid_state_flashes_error_and_redirects(self):
        with _make_auth_app(
            {
                "GOOGLE_CLIENT_ID": "google-client-id",
                "GOOGLE_CLIENT_SECRET": "google-secret",
            }
        ) as (app, _manager):
            with patch("nextreel.web.routes.auth.flash", AsyncMock()) as flash_mock:
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
            with (
                patch(
                    "nextreel.application.auth_flows.httpx.AsyncClient",
                    return_value=_FakeAsyncClient(
                        post_response=token_response,
                        get_response=userinfo_response,
                    ),
                ),
                patch(
                    "session.user_auth.get_user_by_email",
                    AsyncMock(
                        return_value={
                            "user_id": "existing-user",
                            "auth_provider": "email",
                        }
                    ),
                ),
                patch(
                    "session.user_auth.find_or_create_oauth_user", AsyncMock()
                ) as create_oauth_user,
                patch("nextreel.web.routes.auth.flash", AsyncMock()) as flash_mock,
            ):
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
            bound_state = _nav_state(user_id="oauth-user-123")
            bound_state.filters = {"exclude_watched": False}
            app.navigation_state_store.bind_user.return_value = bound_state
            with (
                patch(
                    "nextreel.application.auth_flows.httpx.AsyncClient",
                    return_value=_FakeAsyncClient(
                        post_response=token_response,
                        get_response=userinfo_response,
                    ),
                ),
                patch(
                    "session.user_auth.get_user_by_email",
                    AsyncMock(return_value=None),
                ),
                patch(
                    "session.user_auth.find_or_create_oauth_user",
                    AsyncMock(return_value="oauth-user-123"),
                ) as create_oauth_user,
                patch(
                    "session.user_preferences.get_exclude_watched_default",
                    AsyncMock(return_value=False),
                ) as get_exclude_watched_default,
                patch(
                    "session.user_preferences.get_exclude_watchlist_default",
                    AsyncMock(return_value=True),
                ),
            ):
                async with app.test_request_context(
                    "/auth/google/callback?state=expected&code=abc",
                    method="GET",
                ):
                    initial_state = _nav_state()
                    g.navigation_state = initial_state
                    session[SESSION_OAUTH_STATE_KEY] = "expected"

                    response = await routes.auth_google_callback()
                    attached_state = g.navigation_state

        assert response.status_code == 303
        assert response.location.endswith("/")
        create_oauth_user.assert_awaited_once()
        get_exclude_watched_default.assert_awaited_once_with(
            _manager.db_pool,
            "oauth-user-123",
        )
        app.navigation_state_store.bind_user.assert_awaited_once_with(
            initial_state,
            "oauth-user-123",
            exclude_watched=False,
            exclude_watchlist=True,
        )
        app.navigation_state_store.set_user_id.assert_not_awaited()
        assert attached_state is bound_state


class TestContextProcessor:
    """Regression coverage for inject_csrf_token() — the bridge between
    g.<flag> assignments in route handlers and bare-name lookups in
    Jinja templates (e.g. ``{% if is_in_watchlist %}`` in movie_card.html).
    """

    @pytest.mark.asyncio
    async def test_inject_csrf_token_exposes_is_in_watchlist_from_g(self):
        from nextreel.web.routes.auth import inject_csrf_token

        with _make_auth_app() as (app, _manager):
            async with app.test_request_context("/"):
                g.navigation_state = _nav_state(user_id="user-123")
                g.is_in_watchlist = True

                ctx = inject_csrf_token()

        assert ctx["is_in_watchlist"] is True

    @pytest.mark.asyncio
    async def test_inject_csrf_token_defaults_is_in_watchlist_false(self):
        from nextreel.web.routes.auth import inject_csrf_token

        with _make_auth_app() as (app, _manager):
            async with app.test_request_context("/"):
                g.navigation_state = _nav_state()

                ctx = inject_csrf_token()

        assert ctx["is_in_watchlist"] is False
        assert ctx["is_watched"] is False

    @pytest.mark.asyncio
    async def test_movie_card_template_renders_remove_action_when_in_watchlist(self):
        """End-to-end: g.is_in_watchlist=True must reach Jinja so the form
        submits to /watchlist/remove rather than /watchlist/add.
        """
        from quart import render_template_string

        with _make_auth_app() as (app, _manager):
            async with app.test_request_context("/"):
                g.navigation_state = _nav_state(user_id="user-123")
                g.is_in_watchlist = True

                rendered = await render_template_string(
                    "{% if is_in_watchlist %}/watchlist/remove"
                    "{% else %}/watchlist/add{% endif %}"
                )

        assert rendered == "/watchlist/remove"
