"""Unit tests for auth workflow extraction helpers."""

from __future__ import annotations

import builtins
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeAsyncClient:
    def __init__(self, *, post_response=None, get_response=None):
        self.post = AsyncMock(return_value=post_response)
        self.get = AsyncMock(return_value=get_response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


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


class TestRegistrationService:
    @pytest.mark.asyncio
    async def test_register_email_user_returns_validation_errors(self):
        from nextreel.application.auth_flows import RegistrationService

        outcome = await RegistrationService().register_email_user(
            email="not-an-email",
            password="short",
            confirm_password="different",
            display_name=None,
            db_pool=AsyncMock(),
        )

        assert outcome.kind == "validation_error"
        assert outcome.errors == {
            "email": "Please enter a valid email address.",
            "password": "Password must be at least 8 characters.",
            "confirm_password": "Passwords do not match.",
        }

    @pytest.mark.asyncio
    async def test_register_email_user_returns_duplicate_email_on_precheck(self):
        from nextreel.application.auth_flows import RegistrationService

        with patch(
            "session.user_auth.get_user_by_email",
            AsyncMock(return_value={"user_id": "existing-user"}),
        ), patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")), patch(
            "session.user_auth.register_user", AsyncMock()
        ) as register_user:
            outcome = await RegistrationService().register_email_user(
                email="person@example.com",
                password="password123",
                confirm_password="password123",
                display_name=None,
                db_pool=AsyncMock(),
            )

        assert outcome.kind == "duplicate_email"
        assert outcome.errors == {"email": "An account with this email already exists."}
        register_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_register_email_user_returns_duplicate_email_on_race(self):
        from nextreel.application.auth_flows import RegistrationService
        from session.user_auth import DuplicateUserError

        with patch(
            "session.user_auth.get_user_by_email",
            AsyncMock(return_value=None),
        ), patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")), patch(
            "session.user_auth.register_user",
            AsyncMock(side_effect=DuplicateUserError("person@example.com")),
        ):
            outcome = await RegistrationService().register_email_user(
                email="person@example.com",
                password="password123",
                confirm_password="password123",
                display_name=None,
                db_pool=AsyncMock(),
            )

        assert outcome.kind == "duplicate_email"
        assert outcome.errors == {"email": "An account with this email already exists."}

    @pytest.mark.asyncio
    async def test_register_email_user_returns_user_id_on_success(self):
        from nextreel.application.auth_flows import RegistrationService

        with patch(
            "session.user_auth.get_user_by_email",
            AsyncMock(return_value=None),
        ), patch("session.user_auth.hash_password_async", AsyncMock(return_value="hash")), patch(
            "session.user_auth.register_user", AsyncMock(return_value="user-123")
        ):
            outcome = await RegistrationService().register_email_user(
                email="person@example.com",
                password="password123",
                confirm_password="password123",
                display_name="Pat",
                db_pool=AsyncMock(),
            )

        assert outcome.kind == "success"
        assert outcome.user_id == "user-123"

    @pytest.mark.asyncio
    async def test_register_email_user_returns_service_unavailable_when_bcrypt_missing(self):
        from nextreel.application.auth_flows import RegistrationService

        db_pool = AsyncMock()
        db_pool.execute = AsyncMock(return_value=None)

        with _missing_bcrypt_import():
            outcome = await RegistrationService().register_email_user(
                email="person@example.com",
                password="password123",
                confirm_password="password123",
                display_name=None,
                db_pool=db_pool,
            )

        assert outcome.kind == "service_unavailable"
        assert outcome.errors == {
            "form": "Email/password sign-in is currently unavailable. Please try again later."
        }


class TestGoogleOAuthService:
    def test_build_authorize_url_keeps_current_google_query_contract(self):
        from nextreel.application.auth_flows import GoogleOAuthService

        auth_url = GoogleOAuthService().build_authorize_url(
            oauth_config={
                "google_client_id": "google-client-id",
                "redirect_base": "http://127.0.0.1:5000",
            },
            state_token="expected-state",
        )

        assert auth_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "client_id=google-client-id" in auth_url
        assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A5000%2Fauth%2Fgoogle%2Fcallback" in auth_url
        assert "response_type=code" in auth_url
        assert "scope=openid+email+profile" in auth_url
        assert "state=expected-state" in auth_url

    @pytest.mark.asyncio
    async def test_complete_login_returns_failure_for_invalid_state(self):
        from nextreel.application.auth_flows import GoogleOAuthService

        outcome = await GoogleOAuthService().complete_login(
            oauth_config={
                "google_client_id": "google-client-id",
                "google_client_secret": "google-secret",
                "redirect_base": "http://127.0.0.1:5000",
            },
            expected_state="expected",
            received_state="wrong",
            code="abc",
            db_pool=AsyncMock(),
        )

        assert outcome.kind == "failure"
        assert outcome.error_message == "Google sign-in failed. Please try again."

    @pytest.mark.asyncio
    async def test_complete_login_returns_provider_conflict(self):
        from nextreel.application.auth_flows import GoogleOAuthService

        token_response = MagicMock(status_code=200)
        token_response.json.return_value = {"access_token": "oauth-token"}
        userinfo_response = MagicMock(status_code=200)
        userinfo_response.json.return_value = {
            "email": "person@example.com",
            "sub": "google-subject",
            "name": "Pat Example",
        }

        with patch(
            "nextreel.application.auth_flows.httpx.AsyncClient",
            return_value=_FakeAsyncClient(
                post_response=token_response,
                get_response=userinfo_response,
            ),
        ), patch(
            "session.user_auth.get_user_by_email",
            AsyncMock(return_value={"user_id": "existing-user", "auth_provider": "email"}),
        ), patch(
            "session.user_auth.find_or_create_oauth_user", AsyncMock()
        ) as create_oauth_user:
            outcome = await GoogleOAuthService().complete_login(
                oauth_config={
                    "google_client_id": "google-client-id",
                    "google_client_secret": "google-secret",
                    "redirect_base": "http://127.0.0.1:5000",
                },
                expected_state="expected",
                received_state="expected",
                code="abc",
                db_pool=AsyncMock(),
            )

        assert outcome.kind == "provider_conflict"
        assert outcome.error_message == (
            "An account with this email already exists. Please sign in with email."
        )
        create_oauth_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_complete_login_returns_user_id_on_success(self):
        from nextreel.application.auth_flows import GoogleOAuthService

        token_response = MagicMock(status_code=200)
        token_response.json.return_value = {"access_token": "oauth-token"}
        userinfo_response = MagicMock(status_code=200)
        userinfo_response.json.return_value = {
            "email": "person@example.com",
            "sub": "google-subject",
            "name": "Pat Example",
        }

        with patch(
            "nextreel.application.auth_flows.httpx.AsyncClient",
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
            outcome = await GoogleOAuthService().complete_login(
                oauth_config={
                    "google_client_id": "google-client-id",
                    "google_client_secret": "google-secret",
                    "redirect_base": "http://127.0.0.1:5000",
                },
                expected_state="expected",
                received_state="expected",
                code="abc",
                db_pool=AsyncMock(),
            )

        assert outcome.kind == "success"
        assert outcome.user_id == "oauth-user-123"
        create_oauth_user.assert_awaited_once()
