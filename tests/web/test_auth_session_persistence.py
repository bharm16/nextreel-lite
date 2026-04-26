from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from quart import Quart, g

from infra.filter_normalizer import default_filter_state
from infra.time_utils import utcnow
from nextreel.application.navigation_state_service import NavigationStateService
from nextreel.domain.navigation_state import NavigationState
from nextreel.web.request_context import register_request_context_handlers
from nextreel.web.routes.shared import _attach_user_to_current_session


def _navigation_state(**overrides) -> NavigationState:
    now = utcnow()
    defaults = {
        "session_id": "existing-session-id",
        "version": 1,
        "csrf_token": "csrf-token",
        "filters": default_filter_state(),
        "current_tconst": None,
        "current_ref": None,
        "queue": [],
        "prev": [],
        "future": [],
        "seen": [],
        "created_at": now,
        "last_activity_at": now,
        "expires_at": now + timedelta(hours=1),
        "user_id": "user-123",
    }
    defaults.update(overrides)
    return NavigationState(**defaults)


@pytest.mark.asyncio
async def test_login_binding_marks_existing_navigation_cookie_for_refresh(app):
    initial_state = _navigation_state(user_id=None)
    bound_state = _navigation_state(user_id="user-123")
    app.navigation_state_store = SimpleNamespace(
        bind_user=AsyncMock(return_value=bound_state),
    )
    app.extensions["nextreel"] = SimpleNamespace(
        movie_manager=SimpleNamespace(db_pool=object()),
        metrics_collector=object(),
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "session.user_preferences.get_exclude_watched_default",
            AsyncMock(return_value=True),
        )
        monkeypatch.setattr(
            "session.user_preferences.get_exclude_watchlist_default",
            AsyncMock(return_value=True),
        )
        async with app.test_request_context("/login", method="POST"):
            g.navigation_state = initial_state
            g.set_nr_sid_cookie = False

            result = await _attach_user_to_current_session("user-123")

            assert result is bound_state
            assert g.navigation_state is bound_state
            assert g.set_nr_sid_cookie is True


@pytest.mark.asyncio
async def test_navigation_cookie_uses_configured_max_age_when_refreshed():
    app = Quart(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        NR_SESSION_COOKIE_MAX_AGE=24 * 60 * 60,
        SESSION_COOKIE_SECURE=False,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    app.navigation_state_store = None
    register_request_context_handlers(
        app,
        ensure_movie_manager_started=AsyncMock(),
    )

    @app.get("/")
    async def index():
        g.navigation_state = SimpleNamespace(session_id="existing-session-id")
        g.set_nr_sid_cookie = True
        return "ok"

    response = await app.test_client().get("/")

    set_cookie = response.headers["Set-Cookie"]
    assert "nr_sid=existing-session-id" in set_cookie
    assert "Max-Age=86400" in set_cookie


@pytest.mark.asyncio
async def test_valid_logged_in_navigation_state_loads_after_app_restart_from_cookie():
    state = _navigation_state()
    repository = MagicMock()
    repository.load_state = AsyncMock(return_value=state)
    repository.refresh_activity = AsyncMock()
    migration = MagicMock()
    migration.dual_write_enabled = AsyncMock(return_value=False)
    service = NavigationStateService(repository=repository, migration=migration)

    loaded_state, needs_cookie = await service.load_for_request(state.session_id)

    assert loaded_state.session_id == state.session_id
    assert loaded_state.user_id == "user-123"
    assert needs_cookie is False
    repository.load_state.assert_awaited_once_with(state.session_id)


@pytest.mark.asyncio
async def test_expired_logged_in_navigation_state_is_replaced_with_anonymous_state():
    expired_state = _navigation_state(expires_at=utcnow() - timedelta(seconds=1))
    repository = MagicMock()
    repository.load_state = AsyncMock(return_value=expired_state)
    repository.insert_state = AsyncMock()
    migration = MagicMock()
    migration.dual_write_enabled = AsyncMock(return_value=False)
    service = NavigationStateService(repository=repository, migration=migration)

    loaded_state, needs_cookie = await service.load_for_request(expired_state.session_id)

    assert loaded_state.session_id != expired_state.session_id
    assert loaded_state.user_id is None
    assert needs_cookie is True
    repository.insert_state.assert_awaited_once_with(loaded_state)
