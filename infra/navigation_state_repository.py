from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from infra.cache import CacheNamespace
from infra.filter_normalizer import default_filter_state
from infra.time_utils import env_bool, utcnow
from logging_config import get_logger
from nextreel.domain.navigation_state import (
    FUTURE_STACK_MAX,
    PREV_STACK_MAX,
    QUEUE_TARGET,
    SESSION_COOKIE_MAX_AGE,
    NavigationState,
    _normalize_ref,
    _normalize_ref_list,
    _normalize_seen,
)

logger = get_logger(__name__)


class NavigationStateRepository:
    def __init__(self, db_pool):
        self.db_pool = db_pool
        self._cache = None

    def attach_cache(self, cache) -> None:
        self._cache = cache

    def redis_read_cache_enabled(self) -> bool:
        if self._cache is None:
            return False
        return env_bool("NAV_STATE_REDIS_READ_CACHE_ENABLED", default=False)

    async def invalidate_cached_state(self, session_id: str) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.delete(CacheNamespace.SESSION, f"nav:{session_id}")
        except Exception:
            logger.debug("nav state cache invalidate failed", exc_info=True)

    async def load_state(self, session_id: str) -> NavigationState | None:
        use_cache = self.redis_read_cache_enabled()
        if use_cache:
            cached_state = await self.load_state_from_cache(session_id)
            if cached_state is not None:
                return cached_state
        row = await self.db_pool.execute(
            """
            SELECT session_id, version, csrf_token, filters_json, current_tconst, current_ref_json,
                   queue_json, prev_json, future_json, seen_json,
                   created_at, last_activity_at, expires_at, user_id
            FROM user_navigation_state
            WHERE session_id = %s
            """,
            [session_id],
            fetch="one",
        )
        if not row:
            return None
        state = self.row_to_state(row)
        if use_cache:
            await self.store_state_in_cache(state, row)
        return state

    async def load_state_from_cache(self, session_id: str) -> NavigationState | None:
        try:
            cached = await self._cache.get(CacheNamespace.SESSION, f"nav:{session_id}")
        except Exception:
            return None
        if not isinstance(cached, dict):
            return None
        for field in ("created_at", "last_activity_at", "expires_at"):
            value = cached.get(field)
            if isinstance(value, str):
                try:
                    cached[field] = datetime.fromisoformat(value)
                except ValueError:
                    return None
        try:
            state = self.row_to_state(cached)
        except Exception:
            return None
        if state.expires_at <= utcnow():
            return None
        return state

    async def store_state_in_cache(self, state: NavigationState, row: dict[str, Any]) -> None:
        remaining = int((state.expires_at - utcnow()).total_seconds())
        ttl = max(60, min(remaining, SESSION_COOKIE_MAX_AGE))
        try:
            await self._cache.set(
                CacheNamespace.SESSION,
                f"nav:{state.session_id}",
                row,
                ttl=ttl,
            )
        except Exception:
            logger.debug("nav state cache write failed", exc_info=True)

    def json_load(self, value: Any, fallback: Any) -> Any:
        if value is None:
            return fallback
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return fallback
        return fallback

    def row_to_state(self, row: dict[str, Any]) -> NavigationState:
        filters = self.json_load(row.get("filters_json"), default_filter_state())
        current_ref = _normalize_ref(self.json_load(row.get("current_ref_json"), None))
        return NavigationState(
            session_id=row["session_id"],
            version=int(row["version"]),
            csrf_token=row["csrf_token"],
            filters=filters if isinstance(filters, dict) else default_filter_state(),
            current_tconst=row.get("current_tconst"),
            queue=_normalize_ref_list(
                self.json_load(row.get("queue_json"), []),
                max_items=QUEUE_TARGET,
            ),
            prev=_normalize_ref_list(
                self.json_load(row.get("prev_json"), []),
                max_items=PREV_STACK_MAX,
            ),
            future=_normalize_ref_list(
                self.json_load(row.get("future_json"), []),
                max_items=FUTURE_STACK_MAX,
            ),
            seen=_normalize_seen(self.json_load(row.get("seen_json"), [])),
            created_at=row["created_at"],
            last_activity_at=row["last_activity_at"],
            expires_at=row["expires_at"],
            current_ref=current_ref,
            user_id=row.get("user_id"),
        )

    @staticmethod
    def normalize_current_ref(state: NavigationState) -> dict[str, Any] | None:
        current_ref = _normalize_ref(state.current_ref) if state.current_ref else None
        if current_ref and state.current_tconst and current_ref["tconst"] != state.current_tconst:
            current_ref = {
                "tconst": state.current_tconst,
                "title": current_ref.get("title"),
                "slug": current_ref.get("slug"),
            }
        elif current_ref and state.current_tconst is None:
            state.current_tconst = current_ref["tconst"]
        return current_ref

    def serialized_state_fields(self, state: NavigationState) -> dict[str, Any]:
        cached = state._serialized_cache
        if cached is not None:
            return cached
        current_ref = self.normalize_current_ref(state)
        serialized = {
            "csrf_token": state.csrf_token,
            "user_id": state.user_id,
            "filters_json": json.dumps(state.filters),
            "current_tconst": state.current_tconst,
            "current_ref_json": json.dumps(current_ref) if current_ref else None,
            "queue_json": json.dumps(state.queue),
            "prev_json": json.dumps(state.prev),
            "future_json": json.dumps(state.future),
            "seen_json": json.dumps(state.seen),
        }
        state._serialized_cache = serialized
        return serialized

    async def insert_state(self, state: NavigationState) -> None:
        serialized = self.serialized_state_fields(state)
        await self.db_pool.execute(
            """
            INSERT INTO user_navigation_state (
                session_id, user_id, version, csrf_token, filters_json, current_tconst, current_ref_json,
                queue_json, prev_json, future_json, seen_json,
                created_at, last_activity_at, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                state.session_id,
                state.user_id,
                state.version,
                serialized["csrf_token"],
                serialized["filters_json"],
                serialized["current_tconst"],
                serialized["current_ref_json"],
                serialized["queue_json"],
                serialized["prev_json"],
                serialized["future_json"],
                serialized["seen_json"],
                state.created_at,
                state.last_activity_at,
                state.expires_at,
            ],
            fetch="none",
        )

    async def refresh_activity(
        self,
        session_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        await self.db_pool.execute(
            """
            UPDATE user_navigation_state
            SET last_activity_at = %s, expires_at = %s
            WHERE session_id = %s
            """,
            [now, expires_at, session_id],
            fetch="none",
        )

    async def ready_check(self) -> bool:
        await self.db_pool.execute(
            "SELECT 1 AS ready FROM user_navigation_state LIMIT 1",
            fetch="one",
        )
        return True

    async def save_with_version(
        self,
        state: NavigationState,
        expected_version: int,
        previous_state: NavigationState | None = None,
    ) -> bool:
        next_version = expected_version + 1
        current_values = self.serialized_state_fields(state)
        previous_values = (
            self.serialized_state_fields(previous_state) if previous_state is not None else None
        )

        assignments = ["version = %s"]
        params: list[Any] = [next_version]
        for field in (
            "csrf_token",
            "user_id",
            "filters_json",
            "current_tconst",
            "current_ref_json",
            "queue_json",
            "prev_json",
            "future_json",
            "seen_json",
        ):
            if previous_values is None or current_values[field] != previous_values[field]:
                assignments.append(f"{field} = %s")
                params.append(current_values[field])
        assignments.extend(["last_activity_at = %s", "expires_at = %s"])
        params.extend(
            [state.last_activity_at, state.expires_at, state.session_id, expected_version]
        )
        updated = await self.db_pool.execute(
            f"""
            UPDATE user_navigation_state
            SET {', '.join(assignments)}
            WHERE session_id = %s AND version = %s
            """,
            params,
            fetch="none",
        )
        if updated != 1:
            return False
        await self.invalidate_cached_state(state.session_id)
        return True

    async def set_user_id(self, session_id: str, user_id: str | None) -> None:
        await self.db_pool.execute(
            """
            UPDATE user_navigation_state
            SET user_id = %s, last_activity_at = %s
            WHERE session_id = %s
            """,
            [user_id, utcnow(), session_id],
            fetch="none",
        )
        await self.invalidate_cached_state(session_id)

    async def delete_state(self, session_id: str) -> None:
        await self.db_pool.execute(
            "DELETE FROM user_navigation_state WHERE session_id = %s",
            [session_id],
            fetch="none",
        )
