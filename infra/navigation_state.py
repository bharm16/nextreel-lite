"""MySQL-backed navigation/session state with legacy Redis-session migration."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import random
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, MutableMapping

from filter_contracts import FilterState
from logging_config import get_logger
from session.keys import (
    CRITERIA_KEY,
    CURRENT_FILTERS_KEY,
    CURRENT_MOVIE_KEY,
    FUTURE_STACK_KEY,
    PREVIOUS_STACK_KEY,
    SEEN_TCONSTS_KEY,
    WATCH_QUEUE_KEY,
)

# Filter logic extracted to infra.filter_normalizer — re-exported here
# so all existing ``from infra.navigation_state import X`` continue to work.
from infra.filter_normalizer import (  # noqa: F401
    MAX_FILTER_VALUE_LEN,
    criteria_from_filters,
    default_filter_state,
    filters_from_criteria,
    normalize_filters,
    validate_filters,
)

logger = get_logger(__name__)

SESSION_COOKIE_NAME = "nr_sid"
SESSION_COOKIE_MAX_AGE = 8 * 60 * 60
QUEUE_TARGET = 5
QUEUE_REFILL_THRESHOLD = 2
PREV_STACK_MAX = 20
FUTURE_STACK_MAX = 20
SEEN_MAX = 50


# Re-export from shared utility for backward compatibility.
from infra.cache import CacheNamespace
from infra.time_utils import env_bool, env_int, utcnow  # noqa: F811


def _idle_timeout() -> timedelta:
    return timedelta(minutes=env_int("SESSION_IDLE_TIMEOUT_MINUTES", 15))


def _max_duration() -> timedelta:
    return timedelta(hours=env_int("MAX_SESSION_DURATION_HOURS", 8))


def _normalize_ref(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    tconst = entry.get("tconst") or entry.get("imdb_id")
    if not tconst:
        return None
    return {
        "tconst": tconst,
        "title": entry.get("title"),
        "slug": entry.get("slug"),
    }


def _normalize_ref_list(entries: list[Any], *, max_items: int) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for entry in entries or []:
        ref = _normalize_ref(entry)
        if ref:
            refs.append(ref)
    return refs[:max_items]


def _normalize_seen(entries: list[Any]) -> list[str]:
    seen: list[str] = []
    for entry in entries or []:
        if isinstance(entry, str) and entry:
            seen.append(entry)
    return seen[-SEEN_MAX:]


@dataclass
class NavigationState:
    session_id: str
    version: int
    csrf_token: str
    filters: FilterState
    current_tconst: str | None
    queue: list[dict[str, Any]]
    prev: list[dict[str, Any]]
    future: list[dict[str, Any]]
    seen: list[str]
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    current_ref: dict[str, Any] | None = None
    user_id: str | None = None
    # Memoization slot for ``_serialized_state_fields`` — populated lazily on
    # first serialization, reset to None by ``clone()`` so derived states do
    # not inherit a stale cache. Excluded from repr/eq so it cannot leak into
    # equality comparisons or logs.
    _serialized_cache: dict[str, Any] | None = field(
        default=None, repr=False, compare=False
    )

    def clone(self) -> "NavigationState":
        # Shallow clone: filter values are immutable scalars/lists; queue/prev/future
        # entries are dicts that callers treat as immutable refs (rebuilt, never
        # mutated in place), so copying the outer containers is sufficient and
        # avoids the GC cost of deepcopy on every mutate().
        return NavigationState(
            session_id=self.session_id,
            version=self.version,
            csrf_token=self.csrf_token,
            filters=dict(self.filters) if isinstance(self.filters, dict) else self.filters,
            current_tconst=self.current_tconst,
            queue=[dict(item) for item in self.queue],
            prev=[dict(item) for item in self.prev],
            future=[dict(item) for item in self.future],
            seen=list(self.seen),
            created_at=self.created_at,
            last_activity_at=self.last_activity_at,
            expires_at=self.expires_at,
            current_ref=dict(self.current_ref) if self.current_ref else None,
            user_id=self.user_id,
        )


@dataclass
class MutationResult:
    state: NavigationState | None
    result: Any = None
    conflicted: bool = False


class NavigationStateStore:
    def __init__(self, db_pool):
        self.db_pool = db_pool
        # Migration helper — can be removed once dual-write window closes.
        from infra.legacy_migration import LegacyMigrationHelper

        self.migration = LegacyMigrationHelper(db_pool)
        # Optional write-through Redis read cache, wired by
        # MovieManager.attach_cache. Gated behind
        # NAV_STATE_REDIS_READ_CACHE_ENABLED (default off) so rollout is
        # opt-in until the staleness semantics are validated in staging.
        self._cache = None

    def attach_cache(self, cache) -> None:
        """Attach a cache manager. Idempotent — callers may rebind."""
        self._cache = cache

    def _redis_read_cache_enabled(self) -> bool:
        if self._cache is None:
            return False
        return env_bool("NAV_STATE_REDIS_READ_CACHE_ENABLED", default=False)

    async def _invalidate_cached_state(self, session_id: str) -> None:
        """Delete the Redis read cache entry for ``session_id``.

        Called after every successful mutation so retried callers
        observe fresh state. No-op when the cache isn't attached.
        """
        if self._cache is None:
            return
        try:
            await self._cache.delete(CacheNamespace.SESSION, f"nav:{session_id}")
        except Exception:
            logger.debug("nav state cache invalidate failed", exc_info=True)

    async def dual_write_enabled(self) -> bool:
        return await self.migration.dual_write_enabled()

    async def record_legacy_import(self) -> None:
        await self.migration.record_legacy_import()

    def _fresh_expiry(self, created_at: datetime, now: datetime | None = None) -> datetime:
        current = now or utcnow()
        return min(created_at + _max_duration(), current + _idle_timeout())

    def _fresh_state(self, session_id: str | None = None) -> NavigationState:
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
            expires_at=self._fresh_expiry(created_at, created_at),
            current_ref=None,
        )

    def _state_from_legacy(
        self, session_id: str, legacy_session: MutableMapping[str, Any]
    ) -> NavigationState:
        return self.migration.state_from_legacy(
            legacy_session,
            fresh_state_fn=self._fresh_state,
            filters_from_criteria_fn=filters_from_criteria,
            normalize_ref_fn=_normalize_ref,
            normalize_ref_list_fn=_normalize_ref_list,
            normalize_seen_fn=_normalize_seen,
            queue_target=QUEUE_TARGET,
            prev_max=PREV_STACK_MAX,
            future_max=FUTURE_STACK_MAX,
        )

    async def _load_row(self, session_id: str) -> NavigationState | None:
        if self._redis_read_cache_enabled():
            cached_state = await self._load_row_from_cache(session_id)
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
        state = self._row_to_state(row)
        if self._redis_read_cache_enabled():
            await self._store_row_in_cache(state, row)
        return state

    async def _load_row_from_cache(
        self, session_id: str
    ) -> NavigationState | None:
        """Try to reconstruct state from the Redis read cache.

        The cached blob is the raw row dict (with JSON fields as strings)
        so ``_row_to_state`` can hydrate it the same way as a fresh SELECT.
        Returns None on miss, deserialization failure, or expired row.
        """
        try:
            cached = await self._cache.get(CacheNamespace.SESSION, f"nav:{session_id}")
        except Exception:
            return None
        if not isinstance(cached, dict):
            return None
        # Datetime fields are ISO strings after JSON round-trip; rehydrate.
        for field in ("created_at", "last_activity_at", "expires_at"):
            value = cached.get(field)
            if isinstance(value, str):
                try:
                    cached[field] = datetime.fromisoformat(value)
                except ValueError:
                    return None
        try:
            state = self._row_to_state(cached)
        except Exception:
            return None
        if state.expires_at <= utcnow():
            return None
        return state

    async def _store_row_in_cache(
        self,
        state: NavigationState,
        row: dict[str, Any],
    ) -> None:
        """Write the raw row to Redis so future loads can hydrate it.

        TTL matches the session's remaining lifetime (bounded below at
        60s to avoid pathological short caches).
        """
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

    def _json_load(self, value: Any, fallback: Any) -> Any:
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

    def _row_to_state(self, row: dict[str, Any]) -> NavigationState:
        filters = self._json_load(row.get("filters_json"), default_filter_state())
        current_ref = _normalize_ref(self._json_load(row.get("current_ref_json"), None))
        return NavigationState(
            session_id=row["session_id"],
            version=int(row["version"]),
            csrf_token=row["csrf_token"],
            filters=filters if isinstance(filters, dict) else default_filter_state(),
            current_tconst=row.get("current_tconst"),
            queue=_normalize_ref_list(
                self._json_load(row.get("queue_json"), []), max_items=QUEUE_TARGET
            ),
            prev=_normalize_ref_list(
                self._json_load(row.get("prev_json"), []), max_items=PREV_STACK_MAX
            ),
            future=_normalize_ref_list(
                self._json_load(row.get("future_json"), []), max_items=FUTURE_STACK_MAX
            ),
            seen=_normalize_seen(self._json_load(row.get("seen_json"), [])),
            created_at=row["created_at"],
            last_activity_at=row["last_activity_at"],
            expires_at=row["expires_at"],
            current_ref=current_ref,
            user_id=row.get("user_id"),
        )

    @staticmethod
    def _normalize_current_ref(state: NavigationState) -> dict[str, Any] | None:
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

    def _serialized_state_fields(self, state: NavigationState) -> dict[str, Any]:
        cached = state._serialized_cache
        if cached is not None:
            return cached
        current_ref = self._normalize_current_ref(state)
        serialized = {
            "csrf_token": state.csrf_token,
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

    async def _insert_state(self, state: NavigationState) -> None:
        serialized = self._serialized_state_fields(state)
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

    async def _touch_if_needed(self, state: NavigationState) -> NavigationState:
        now = utcnow()
        if now <= state.last_activity_at + timedelta(minutes=1):
            return state

        expires_at = self._fresh_expiry(state.created_at, now)
        await self.db_pool.execute(
            """
            UPDATE user_navigation_state
            SET last_activity_at = %s, expires_at = %s
            WHERE session_id = %s
            """,
            [now, expires_at, state.session_id],
            fetch="none",
        )
        state.last_activity_at = now
        state.expires_at = expires_at
        return state

    async def load_for_request(
        self,
        cookie_session_id: str | None,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> tuple[NavigationState, bool]:
        if cookie_session_id:
            state = await self._load_row(cookie_session_id)
            if state and state.expires_at > utcnow():
                return await self._touch_if_needed(state), False

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
            state = self._state_from_legacy(uuid.uuid4().hex, legacy_session)
            await self._insert_state(state)
            await self.record_legacy_import()
            return state, True

        if dual_write:
            from infra.metrics import navigation_state_migration_miss_total

            navigation_state_migration_miss_total.inc()

        state = self._fresh_state()
        await self._insert_state(state)
        return state, True

    async def get_state(self, session_id: str) -> NavigationState | None:
        state = await self._load_row(session_id)
        if state and state.expires_at > utcnow():
            return state
        return None

    async def ready_check(self) -> bool:
        await self.db_pool.execute(
            "SELECT 1 AS ready FROM user_navigation_state LIMIT 1",
            fetch="one",
        )
        return True

    async def save_state(
        self,
        state: NavigationState,
        expected_version: int,
        previous_state: NavigationState | None = None,
    ) -> bool:
        now = utcnow()
        state.last_activity_at = now
        state.expires_at = self._fresh_expiry(state.created_at, now)
        next_version = expected_version + 1
        current_values = self._serialized_state_fields(state)
        previous_values = (
            self._serialized_state_fields(previous_state) if previous_state is not None else None
        )

        assignments = ["version = %s"]
        params: list[Any] = [next_version]
        for field in (
            "csrf_token",
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
        state.version = next_version
        await self._invalidate_cached_state(state.session_id)
        return True

    def _write_legacy_session(
        self, state: NavigationState, legacy_session: MutableMapping[str, Any]
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
                    self._write_legacy_session(working, legacy_session)
                return MutationResult(state=working, result=result, conflicted=False)

            navigation_state_conflicts_total.inc()
            current_state = None
            # Exponential backoff + jitter on conflict to avoid retry storms
            # under per-session contention (rapid double-clicks, browser retries).
            if attempt < max_attempts - 1:
                # Full jitter: sleep uniformly in [0, base_backoff] for
                # better thundering-herd resistance than narrow ±10ms jitter.
                base_backoff_ms = 10 * (2 ** attempt)
                backoff_ms = random.randint(0, base_backoff_ms)
                await asyncio.sleep(backoff_ms / 1000.0)

        return MutationResult(state=await self.get_state(session_id), conflicted=True)

    async def set_user_id(self, session_id: str, user_id: str | None) -> None:
        """Link or unlink a user account to/from a session."""
        await self.db_pool.execute(
            """
            UPDATE user_navigation_state
            SET user_id = %s, last_activity_at = %s
            WHERE session_id = %s
            """,
            [user_id, utcnow(), session_id],
            fetch="none",
        )

    async def delete_state(
        self, session_id: str, legacy_session: MutableMapping[str, Any] | None = None
    ) -> None:
        await self.db_pool.execute(
            "DELETE FROM user_navigation_state WHERE session_id = %s",
            [session_id],
            fetch="none",
        )
        if legacy_session is not None:
            legacy_session.clear()
