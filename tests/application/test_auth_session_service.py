from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class TestAuthenticatedSessionBinder:
    @pytest.mark.asyncio
    async def test_bind_user_loads_watched_preference_and_updates_navigation_state(self):
        from nextreel.application.auth_session_service import AuthenticatedSessionBinder

        db_pool = object()
        initial_state = SimpleNamespace(session_id="session-123", filters={})
        bound_state = SimpleNamespace(
            session_id="session-123",
            user_id="user-123",
            filters={"exclude_watched": True},
        )
        navigation_state_store = SimpleNamespace(
            bind_user=AsyncMock(return_value=bound_state),
        )

        with patch(
            "session.user_preferences.get_exclude_watched_default",
            AsyncMock(return_value=True),
        ) as get_exclude_watched_default:
            result = await AuthenticatedSessionBinder().bind_user(
                db_pool=db_pool,
                navigation_state_store=navigation_state_store,
                state=initial_state,
                user_id="user-123",
            )

        assert result is bound_state
        get_exclude_watched_default.assert_awaited_once_with(db_pool, "user-123")
        navigation_state_store.bind_user.assert_awaited_once_with(
            initial_state,
            "user-123",
            exclude_watched=True,
        )

    @pytest.mark.asyncio
    async def test_bind_user_preserves_navigation_conflict_as_none(self):
        from nextreel.application.auth_session_service import AuthenticatedSessionBinder

        db_pool = object()
        initial_state = SimpleNamespace(session_id="session-123", filters={})
        navigation_state_store = SimpleNamespace(
            bind_user=AsyncMock(return_value=None),
        )

        with patch(
            "session.user_preferences.get_exclude_watched_default",
            AsyncMock(return_value=False),
        ):
            result = await AuthenticatedSessionBinder().bind_user(
                db_pool=db_pool,
                navigation_state_store=navigation_state_store,
                state=initial_state,
                user_id="user-123",
            )

        assert result is None
        navigation_state_store.bind_user.assert_awaited_once_with(
            initial_state,
            "user-123",
            exclude_watched=False,
        )
