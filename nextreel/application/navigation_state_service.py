from __future__ import annotations

import asyncio
import inspect
import random
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, MutableMapping

from infra.filter_normalizer import (
    criteria_from_filters,
    default_filter_state,
    filters_from_criteria,
)
from infra.navigation_state_repository import NavigationStateRepository
from infra.time_utils import env_int, utcnow
from nextreel.domain.navigation_state import (
    FUTURE_STACK_MAX,
    PREV_STACK_MAX,
    QUEUE_TARGET,
    MutationResult,
    NavigationState,
    _normalize_ref,
    _normalize_ref_list,
    _normalize_seen,
)
from session.keys import (
    CRITERIA_KEY,
    CURRENT_MOVIE_KEY,
    FUTURE_STACK_KEY,
    PREVIOUS_STACK_KEY,
    WATCH_QUEUE_KEY,
)


def _idle_timeout() -> timedelta:
    return timedelta(minutes=env_int("SESSION_IDLE_TIMEOUT_MINUTES", 15))


def _max_duration() -> timedelta:
    return timedelta(hours=env_int("MAX_SESSION_DURATION_HOURS", 8))


class NavigationStateService:
    def __init__(self, repository: NavigationStateRepository, migration):
        self.repository = repository
        self.migration = migration

    async def dual_write_enabled(self) -> bool:
        return await self.migration.dual_write_enabled()

    async def record_legacy_import(self) -> None:
        await self.migration.record_legacy_import()

    def fresh_expiry(self, created_at: datetime, now: datetime | None = None) -> datetime:
        current = now or utcnow()
        return min(created_at + _max_duration(), current + _idle_timeout())

    def fresh_state(self, session_id: str | None = None) -> NavigationState:
        created_at = utcnow()
        return NavigationState(
            session_id=session_id or uuid.uuid4().hex,
            version=1,
            csrf_token=secrets.token_hex(32),
            filters=default_filter_state(),
            current_tconst=None,
            queue=[],
            prev=[],
            future=[],
            seen=[],
            created_at=created_at,
            last_activity_at=created_at,
            expires_at=self.fresh_expiry(created_at, created_at),
            current_ref=None,
        )

    def state_from_legacy(
        self,
        session_id: str,
        legacy_session: MutableMapping[str, Any],
    ) -> NavigationState:
        return self.migration.state_from_legacy(
            legacy_session,
            fresh_state_fn=self.fresh_state,
            filters_from_criteria_fn=filters_from_criteria,
            normalize_ref_fn=_normalize_ref,
            normalize_ref_list_fn=_normalize_ref_list,
            normalize_seen_fn=_normalize_seen,
            queue_target=QUEUE_TARGET,
            prev_max=PREV_STACK_MAX,
            future_max=FUTURE_STACK_MAX,
        )

    async def touch_if_needed(self, state: NavigationState) -> NavigationState:
        now = utcnow()
        if now <= state.last_activity_at + timedelta(minutes=1):
            return state

        expires_at = self.fresh_expiry(state.created_at, now)
        await self.repository.refresh_activity(state.session_id, now, expires_at)
        state.last_activity_at = now
        state.expires_at = expires_at
        return state

    async def load_for_request(
        self,
        cookie_session_id: str | None,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> tuple[NavigationState, bool]:
        if cookie_session_id:
            state = await self.repository.load_state(cookie_session_id)
            if state and state.expires_at > utcnow():
                return await self.touch_if_needed(state), False

        dual_write = await self.dual_write_enabled()
        if (
            dual_write
            and legacy_session
            and (
                legacy_session.get(CURRENT_MOVIE_KEY)
                or legacy_session.get(WATCH_QUEUE_KEY)
                or legacy_session.get(PREVIOUS_STACK_KEY)
                or legacy_session.get(FUTURE_STACK_KEY)
                or legacy_session.get(CRITERIA_KEY)
            )
        ):
            state = self.state_from_legacy(uuid.uuid4().hex, legacy_session)
            await self.repository.insert_state(state)
            await self.record_legacy_import()
            return state, True

        if dual_write:
            from infra.metrics import navigation_state_migration_miss_total

            navigation_state_migration_miss_total.inc()

        state = self.fresh_state()
        await self.repository.insert_state(state)
        return state, True

    async def get_state(self, session_id: str) -> NavigationState | None:
        state = await self.repository.load_state(session_id)
        if state and state.expires_at > utcnow():
            return state
        return None

    async def ready_check(self) -> bool:
        return await self.repository.ready_check()

    async def save_state(
        self,
        state: NavigationState,
        expected_version: int,
        previous_state: NavigationState | None = None,
    ) -> bool:
        now = utcnow()
        state.last_activity_at = now
        state.expires_at = self.fresh_expiry(state.created_at, now)
        saved = await self.repository.save_with_version(
            state,
            expected_version=expected_version,
            previous_state=previous_state,
        )
        if saved:
            state.version = expected_version + 1
        return saved

    def write_legacy_session(
        self,
        state: NavigationState,
        legacy_session: MutableMapping[str, Any],
    ) -> None:
        self.migration.write_legacy_session(state, legacy_session, criteria_from_filters)

    async def mutate(
        self,
        session_id: str,
        mutator: Callable[[NavigationState], Any | Awaitable[Any]],
        legacy_session: MutableMapping[str, Any] | None = None,
        current_state: NavigationState | None = None,
    ) -> MutationResult:
        from infra.metrics import navigation_state_conflicts_total

        max_attempts = 5
        for attempt in range(max_attempts):
            current = (
                current_state.clone()
                if current_state is not None
                else await self.get_state(session_id)
            )
            if not current:
                return MutationResult(state=None, conflicted=True)

            working = current.clone()
            result = mutator(working)
            if inspect.isawaitable(result):
                result = await result

            if await self.save_state(
                working,
                expected_version=current.version,
                previous_state=current,
            ):
                if legacy_session and await self.dual_write_enabled():
                    self.write_legacy_session(working, legacy_session)
                return MutationResult(state=working, result=result, conflicted=False)

            navigation_state_conflicts_total.inc()
            current_state = None
            if attempt < max_attempts - 1:
                base_backoff_ms = 10 * (2**attempt)
                backoff_ms = random.randint(0, base_backoff_ms)
                await asyncio.sleep(backoff_ms / 1000.0)

        return MutationResult(state=await self.get_state(session_id), conflicted=True)

    async def set_user_id(self, session_id: str, user_id: str | None) -> None:
        await self.repository.set_user_id(session_id, user_id)

    async def bind_user(
        self,
        state: NavigationState,
        user_id: str,
        *,
        exclude_watched: bool,
    ) -> NavigationState | None:
        def mutator(working: NavigationState) -> NavigationState:
            working.user_id = user_id
            working.filters = dict(working.filters)
            working.filters["exclude_watched"] = exclude_watched
            return working

        result = await self.mutate(
            state.session_id,
            mutator,
            current_state=state,
        )
        if result.conflicted:
            return None
        return result.state

    async def delete_state(
        self,
        session_id: str,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> None:
        await self.repository.delete_state(session_id)
        if legacy_session is not None:
            legacy_session.clear()


class NavigationStateStore:
    """Compatibility facade for existing callers of infra.navigation_state."""

    def __init__(self, db_pool):
        self.db_pool = db_pool
        from infra.legacy_migration import LegacyMigrationHelper

        self.migration = LegacyMigrationHelper(db_pool)
        self.repository = NavigationStateRepository(db_pool)
        self.service = NavigationStateService(self.repository, self.migration)
        self._cache = None

    def attach_cache(self, cache) -> None:
        self._cache = cache
        self.repository.attach_cache(cache)

    def _redis_read_cache_enabled(self) -> bool:
        return self.repository.redis_read_cache_enabled()

    async def _invalidate_cached_state(self, session_id: str) -> None:
        await self.repository.invalidate_cached_state(session_id)

    async def dual_write_enabled(self) -> bool:
        return await self.service.dual_write_enabled()

    async def record_legacy_import(self) -> None:
        await self.service.record_legacy_import()

    def _fresh_expiry(self, created_at: datetime, now: datetime | None = None) -> datetime:
        return self.service.fresh_expiry(created_at, now)

    def _fresh_state(self, session_id: str | None = None) -> NavigationState:
        return self.service.fresh_state(session_id)

    def _state_from_legacy(
        self,
        session_id: str,
        legacy_session: MutableMapping[str, Any],
    ) -> NavigationState:
        return self.service.state_from_legacy(session_id, legacy_session)

    async def _load_row(self, session_id: str) -> NavigationState | None:
        return await self.repository.load_state(session_id)

    async def _load_row_from_cache(self, session_id: str) -> NavigationState | None:
        return await self.repository.load_state_from_cache(session_id)

    async def _store_row_in_cache(
        self,
        state: NavigationState,
        row: dict[str, Any],
    ) -> None:
        await self.repository.store_state_in_cache(state, row)

    def _json_load(self, value: Any, fallback: Any) -> Any:
        return self.repository.json_load(value, fallback)

    def _row_to_state(self, row: dict[str, Any]) -> NavigationState:
        return self.repository.row_to_state(row)

    @staticmethod
    def _normalize_current_ref(state: NavigationState) -> dict[str, Any] | None:
        return NavigationStateRepository.normalize_current_ref(state)

    def _serialized_state_fields(self, state: NavigationState) -> dict[str, Any]:
        return self.repository.serialized_state_fields(state)

    async def _insert_state(self, state: NavigationState) -> None:
        await self.repository.insert_state(state)

    async def _touch_if_needed(self, state: NavigationState) -> NavigationState:
        return await self.service.touch_if_needed(state)

    async def load_for_request(
        self,
        cookie_session_id: str | None,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> tuple[NavigationState, bool]:
        return await self.service.load_for_request(cookie_session_id, legacy_session)

    async def get_state(self, session_id: str) -> NavigationState | None:
        return await self.service.get_state(session_id)

    async def ready_check(self) -> bool:
        return await self.service.ready_check()

    async def save_state(
        self,
        state: NavigationState,
        expected_version: int,
        previous_state: NavigationState | None = None,
    ) -> bool:
        return await self.service.save_state(state, expected_version, previous_state)

    def _write_legacy_session(
        self,
        state: NavigationState,
        legacy_session: MutableMapping[str, Any],
    ) -> None:
        self.service.write_legacy_session(state, legacy_session)

    async def mutate(
        self,
        session_id: str,
        mutator: Callable[[NavigationState], Any | Awaitable[Any]],
        legacy_session: MutableMapping[str, Any] | None = None,
        current_state: NavigationState | None = None,
    ) -> MutationResult:
        return await self.service.mutate(session_id, mutator, legacy_session, current_state)

    async def set_user_id(self, session_id: str, user_id: str | None) -> None:
        await self.service.set_user_id(session_id, user_id)

    async def bind_user(
        self,
        state: NavigationState,
        user_id: str,
        *,
        exclude_watched: bool,
    ) -> NavigationState | None:
        return await self.service.bind_user(
            state,
            user_id,
            exclude_watched=exclude_watched,
        )

    async def delete_state(
        self,
        session_id: str,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> None:
        await self.service.delete_state(session_id, legacy_session)
