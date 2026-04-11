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

    @pytest.mark.asyncio
    async def test_bind_user_sets_user_id_and_exclude_watched_through_mutate(self):
        from infra.navigation_state import MutationResult, NavigationStateService

        repository = MagicMock()
        migration = MagicMock()
        service = NavigationStateService(repository=repository, migration=migration)
        original_filters = {"genres": ["Drama"], "exclude_watched": True}
        state = _make_state(filters=original_filters)

        async def fake_mutate(session_id, mutator, legacy_session=None, current_state=None):
            working = current_state.clone()
            result = mutator(working)
            if hasattr(result, "__await__"):
                await result
            return MutationResult(state=working, result=working, conflicted=False)

        service.mutate = AsyncMock(side_effect=fake_mutate)

        updated = await service.bind_user(state, "user-123", exclude_watched=False)

        service.mutate.assert_awaited_once()
        session_id, _mutator = service.mutate.await_args.args[:2]
        assert session_id == "test-session"
        assert service.mutate.await_args.kwargs["current_state"] is state
        assert updated.user_id == "user-123"
        assert updated.filters == {"genres": ["Drama"], "exclude_watched": False}
        assert updated.filters is not original_filters
        assert original_filters == {"genres": ["Drama"], "exclude_watched": True}

    @pytest.mark.asyncio
    async def test_bind_user_returns_none_on_mutation_conflict(self):
        from infra.navigation_state import MutationResult, NavigationStateService

        service = NavigationStateService(repository=MagicMock(), migration=MagicMock())
        state = _make_state()
        service.mutate = AsyncMock(
            return_value=MutationResult(state=state, result=None, conflicted=True)
        )

        updated = await service.bind_user(state, "user-123", exclude_watched=True)

        assert updated is None


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

    @pytest.mark.asyncio
    async def test_save_with_version_persists_changed_user_id(self):
        from infra.navigation_state import NavigationStateRepository

        db_pool = AsyncMock()
        db_pool.execute = AsyncMock(return_value=1)
        repository = NavigationStateRepository(db_pool)
        previous_state = _make_state(user_id=None)
        state = previous_state.clone()
        state.user_id = "user-123"

        saved = await repository.save_with_version(
            state,
            expected_version=1,
            previous_state=previous_state,
        )

        assert saved is True
        sql = db_pool.execute.await_args.args[0]
        params = db_pool.execute.await_args.args[1]
        assert "user_id = %s" in sql
        assert "user-123" in params
