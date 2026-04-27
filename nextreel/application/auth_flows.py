"""Auth workflow helpers for route-level orchestration."""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlencode

import httpx


_REGISTER_DUPLICATE_EMAIL = "An account with this email already exists."
_GOOGLE_FAILURE_MESSAGE = "Google sign-in failed. Please try again."


@dataclass(slots=True)
class RegistrationOutcome:
    kind: Literal["success", "validation_error", "duplicate_email", "service_unavailable"]
    user_id: str | None = None
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class OAuthOutcome:
    kind: Literal["success", "provider_conflict", "failure"]
    user_id: str | None = None
    error_message: str | None = None


class RegistrationService:
    async def register_email_user(
        self,
        *,
        email: str,
        password: str,
        confirm_password: str,
        display_name: str | None,
        db_pool,
    ) -> RegistrationOutcome:
        from session.user_auth import (
            DuplicateUserError,
            EMAIL_PASSWORD_AUTH_UNAVAILABLE_MESSAGE,
            EmailPasswordAuthUnavailableError,
            get_user_by_email,
            hash_password_async,
            register_user,
            validate_registration,
        )

        errors = validate_registration(email, password, confirm_password)
        if errors:
            return RegistrationOutcome(kind="validation_error", errors=errors)

        hash_task = asyncio.create_task(hash_password_async(password))
        try:
            existing = await get_user_by_email(db_pool, email)
            if existing:
                hash_task.cancel()
                await asyncio.gather(hash_task, return_exceptions=True)
                return RegistrationOutcome(
                    kind="duplicate_email",
                    errors={"email": _REGISTER_DUPLICATE_EMAIL},
                )

            password_hash = await hash_task
        except EmailPasswordAuthUnavailableError:
            return RegistrationOutcome(
                kind="service_unavailable",
                errors={"form": EMAIL_PASSWORD_AUTH_UNAVAILABLE_MESSAGE},
            )
        except BaseException:
            if not hash_task.done():
                hash_task.cancel()
                await asyncio.gather(hash_task, return_exceptions=True)
            raise

        try:
            user_id = await register_user(
                db_pool,
                email,
                password,
                display_name,
                precomputed_hash=password_hash,
            )
        except DuplicateUserError:
            return RegistrationOutcome(
                kind="duplicate_email",
                errors={"email": _REGISTER_DUPLICATE_EMAIL},
            )

        return RegistrationOutcome(kind="success", user_id=user_id)


class GoogleOAuthService:
    def build_authorize_url(self, *, oauth_config: dict, state_token: str) -> str:
        redirect_uri = "%s/auth/google/callback" % oauth_config["redirect_base"]
        params = urlencode({
            "client_id": oauth_config["google_client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state_token,
        })
        return "https://accounts.google.com/o/oauth2/v2/auth?%s" % params

    async def complete_login(
        self,
        *,
        oauth_config: dict,
        expected_state: str | None,
        received_state: str,
        code: str | None,
        db_pool,
    ) -> OAuthOutcome:
        from session.user_auth import find_or_create_oauth_user, get_user_by_email

        if not expected_state or not hmac.compare_digest(expected_state, received_state):
            return OAuthOutcome(kind="failure", error_message=_GOOGLE_FAILURE_MESSAGE)

        if not code:
            return OAuthOutcome(kind="failure", error_message=_GOOGLE_FAILURE_MESSAGE)

        redirect_uri = "%s/auth/google/callback" % oauth_config["redirect_base"]

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": oauth_config["google_client_id"],
                    "client_secret": oauth_config["google_client_secret"],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if token_response.status_code != 200:
                return OAuthOutcome(kind="failure", error_message=_GOOGLE_FAILURE_MESSAGE)

            tokens = token_response.json()

            userinfo_response = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if userinfo_response.status_code != 200:
                return OAuthOutcome(kind="failure", error_message=_GOOGLE_FAILURE_MESSAGE)

            userinfo = userinfo_response.json()

        email = userinfo.get("email")
        oauth_sub = userinfo.get("sub")
        display_name = userinfo.get("name")

        if not email or not oauth_sub:
            return OAuthOutcome(kind="failure", error_message=_GOOGLE_FAILURE_MESSAGE)

        existing = await get_user_by_email(db_pool, email)
        if existing and existing["auth_provider"] != "google":
            provider = existing["auth_provider"]
            return OAuthOutcome(
                kind="provider_conflict",
                error_message=(
                    "An account with this email already exists. Please sign in with %s."
                    % provider
                ),
            )

        user_id = await find_or_create_oauth_user(
            db_pool,
            provider="google",
            oauth_sub=oauth_sub,
            email=email,
            display_name=display_name,
        )
        return OAuthOutcome(kind="success", user_id=user_id)
