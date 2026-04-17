"""Route tests for the redesigned single-page account settings."""

from __future__ import annotations

import os
from contextlib import contextmanager, ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import TEST_ENV


def _nav_state(*, user_id=None):
    return SimpleNamespace(
        session_id="test-session-id",
        csrf_token="test-csrf-token",
        filters={},
        user_id=user_id,
    )


@contextmanager
def _make_account_app(*, authenticated=False):
    """Create a test app with optional authentication.

    When ``authenticated=False`` the navigation_state_store is left as ``None``
    so the before_request handler takes the TESTING shortcut and creates an
    anonymous state (user_id=None).

    When ``authenticated=True`` we need the request context to load a state
    with user_id set.  We accomplish this by:
    1. Making ``movie_manager.start`` an AsyncMock so ``ensure_movie_manager_started``
       completes without error.
    2. Setting ``app.navigation_state_store`` to an AsyncMock whose
       ``load_for_request`` returns a state with user_id="u1".
    """
    env = {**TEST_ENV}
    with patch.dict(os.environ, env, clear=False), patch("app.MovieManager") as MockMgr:
        manager = MockMgr.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool.execute = AsyncMock(return_value=None)
        manager.start = AsyncMock()

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True

        if authenticated:
            state = _nav_state(user_id="u1")
            store = AsyncMock()
            store.load_for_request = AsyncMock(return_value=(state, False))
            store.set_user_id = AsyncMock()
            store.bind_user = AsyncMock()
            # ensure_movie_manager_started does:
            #   app.navigation_state_store = movie_manager.navigation_state_store
            # so we must pre-wire the manager's attribute to our store so it
            # survives the startup sequence.
            manager.navigation_state_store = store
            app.navigation_state_store = store
        else:
            # Anonymous: before_request uses build_test_navigation_state() (user_id=None)
            app.navigation_state_store = None

        yield app, manager


def _patch_user(user=None):
    """Patch get_user_by_id to return a fake user dict."""
    if user is None:
        user = {
            "user_id": "u1",
            "email": "test@example.com",
            "display_name": "Test User",
            "auth_provider": "email",
            "created_at": None,
        }
    return patch(
        "nextreel.web.routes.account.get_user_by_id",
        new_callable=AsyncMock,
        return_value=user,
    )


def _patch_prefs():
    """Return a list of context managers that patch all preference getters."""
    return [
        patch(
            "nextreel.web.routes.account.user_preferences.get_exclude_watched_default",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "nextreel.web.routes.account.user_preferences.get_theme_preference",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "nextreel.web.routes.account.user_preferences.get_default_filters",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ]


# ── GET /account ──────────────────────────────────────────────────────────────


async def test_account_redirects_unauthenticated():
    """Unauthenticated requests to /account redirect to the login page."""
    with _make_account_app(authenticated=False) as (app, _):
        client = app.test_client()
        resp = await client.get("/account")
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/login" in location


async def test_account_renders_single_page():
    """Authenticated GET /account returns 200 with all settings sections."""
    with _make_account_app(authenticated=True) as (app, _):
        client = app.test_client()
        with ExitStack() as stack:
            stack.enter_context(_patch_user())
            for p in _patch_prefs():
                stack.enter_context(p)
            resp = await client.get("/account")
            assert resp.status_code == 200
            body = await resp.get_data(as_text=True)
            # Top-level page marker
            assert "settings-page" in body
            assert "Account" in body
            # All expected sections
            assert "Profile" in body
            assert "Preferences" in body
            assert "Security" in body
            assert "Data" in body
            assert "Danger Zone" in body


async def test_account_ignores_old_tab_param():
    """GET /account?tab=security should still return 200 (tab params are ignored)."""
    with _make_account_app(authenticated=True) as (app, _):
        client = app.test_client()
        with ExitStack() as stack:
            stack.enter_context(_patch_user())
            for p in _patch_prefs():
                stack.enter_context(p)
            resp = await client.get("/account?tab=security")
            assert resp.status_code == 200


# ── POST /account/preferences ────────────────────────────────────────────────


async def test_preferences_save_without_theme_field_preserves_theme_preference():
    """The redesigned preferences form must not clear theme when it omits the field."""
    with _make_account_app(authenticated=True) as (app, _):
        client = app.test_client()
        with (
            patch(
                "nextreel.web.routes.account.user_preferences.set_exclude_watched_default",
                new_callable=AsyncMock,
            ) as set_exclude_watched_default,
            patch(
                "nextreel.web.routes.account.user_preferences.set_theme_preference",
                new_callable=AsyncMock,
            ) as set_theme_preference,
        ):
            resp = await client.post(
                "/account/preferences",
                form={
                    "exclude_watched_default": "on",
                    "csrf_token": "test-csrf-token",
                },
            )

            assert resp.status_code == 302
            set_exclude_watched_default.assert_awaited_once()
            set_theme_preference.assert_not_awaited()


# ── POST /account/delete ──────────────────────────────────────────────────────


async def test_delete_rejects_wrong_confirmation():
    """POST /account/delete with wrong confirmation text returns 400."""
    with _make_account_app(authenticated=True) as (app, _):
        client = app.test_client()
        with _patch_user():
            resp = await client.post(
                "/account/delete",
                form={"confirm_delete": "wrong", "csrf_token": "test-csrf-token"},
            )
            assert resp.status_code == 400


async def test_delete_rejects_email_as_confirmation():
    """POST /account/delete with the user's email (old behaviour) returns 400."""
    with _make_account_app(authenticated=True) as (app, _):
        client = app.test_client()
        with _patch_user():
            resp = await client.post(
                "/account/delete",
                form={
                    "confirm_delete": "test@example.com",
                    "csrf_token": "test-csrf-token",
                },
            )
            assert resp.status_code == 400


async def test_delete_accepts_typed_delete():
    """POST /account/delete with confirm_delete='delete' succeeds and redirects."""
    with _make_account_app(authenticated=True) as (app, _):
        client = app.test_client()
        with (
            _patch_user(),
            patch(
                "nextreel.web.routes.account._redis_client",
                return_value=None,
            ),
        ):
            resp = await client.post(
                "/account/delete",
                form={"confirm_delete": "delete", "csrf_token": "test-csrf-token"},
            )
            # Should redirect (302/303) after successful deletion
            assert resp.status_code in (302, 303)
