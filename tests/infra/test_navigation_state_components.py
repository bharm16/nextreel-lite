"""Unit tests for extracted navigation state repository/service components."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from infra.navigation_state import NavigationState, default_filter_state, utcnow


def _make_state(**overrides) -> NavigationState:
    now = utcnow()
    defaults = dict(
        session_id="test-session",
        version=1,
        csrf_token="tok",
        filters=default_filter_state(),
        current_tconst=None,
        current_ref=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
    )
    defaults.update(overrides)
    return NavigationState(**defaults)


class TestNavigationStateService:
    @pytest.mark.asyncio
    async def test_load_for_request_returns_existing_state_without_new_cookie(self):
        from infra.navigation_state import NavigationStateService

        repository = MagicMock()
        state = _make_state()
        repository.load_state = AsyncMock(return_value=state)
        repository.refresh_activity = AsyncMock(return_value=state)
        migration = MagicMock()

        service = NavigationStateService(repository=repository, migration=migration)

        loaded_state, needs_cookie = await service.load_for_request("test-session")

        assert loaded_state.session_id == "test-session"
        assert needs_cookie is False


class TestNavigationStateRepository:
    @pytest.mark.asyncio
    async def test_save_with_version_invalidates_cache_on_success(self):
        from infra.navigation_state import NavigationStateRepository

        db_pool = AsyncMock()
        db_pool.execute = AsyncMock(return_value=1)
        repository = NavigationStateRepository(db_pool)
        repository.attach_cache(AsyncMock())
        state = _make_state()

        saved = await repository.save_with_version(
            state,
            expected_version=1,
            previous_state=state.clone(),
        )

        assert saved is True
        repository._cache.delete.assert_awaited_once()
