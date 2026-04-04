"""MySQL-backed navigation/session state with legacy Redis-session migration."""

from __future__ import annotations

import copy
import inspect
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, MutableMapping

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
from infra.time_utils import utcnow  # noqa: F811


def _idle_timeout() -> timedelta:
    return timedelta(minutes=int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", 15)))


def _max_duration() -> timedelta:
    return timedelta(hours=int(os.getenv("MAX_SESSION_DURATION_HOURS", 8)))


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
    filters: dict[str, Any]
    current_tconst: str | None
    queue: list[dict[str, Any]]
    prev: list[dict[str, Any]]
    future: list[dict[str, Any]]
    seen: list[str]
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    current_ref: dict[str, Any] | None = None

    def clone(self) -> "NavigationState":
        return NavigationState(
            session_id=self.session_id,
            version=self.version,
            csrf_token=self.csrf_token,
            filters=copy.deepcopy(self.filters),
            current_tconst=self.current_tconst,
            queue=copy.deepcopy(self.queue),
            prev=copy.deepcopy(self.prev),
            future=copy.deepcopy(self.future),
            seen=list(self.seen),
            created_at=self.created_at,
            last_activity_at=self.last_activity_at,
            expires_at=self.expires_at,
            current_ref=copy.deepcopy(self.current_ref),
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

    def _state_from_legacy(self, session_id: str, legacy_session: MutableMapping[str, Any]) -> NavigationState:
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
        row = await self.db_pool.execute(
            """
            SELECT session_id, version, csrf_token, filters_json, current_tconst, current_ref_json,
                   queue_json, prev_json, future_json, seen_json,
                   created_at, last_activity_at, expires_at
            FROM user_navigation_state
            WHERE session_id = %s
            """,
            [session_id],
            fetch="one",
        )
        if not row:
            return None
        return self._row_to_state(row)

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
            queue=_normalize_ref_list(self._json_load(row.get("queue_json"), []), max_items=QUEUE_TARGET),
            prev=_normalize_ref_list(self._json_load(row.get("prev_json"), []), max_items=PREV_STACK_MAX),
            future=_normalize_ref_list(self._json_load(row.get("future_json"), []), max_items=FUTURE_STACK_MAX),
            seen=_normalize_seen(self._json_load(row.get("seen_json"), [])),
            created_at=row["created_at"],
            last_activity_at=row["last_activity_at"],
            expires_at=row["expires_at"],
            current_ref=current_ref,
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
        current_ref = self._normalize_current_ref(state)
        return {
            "csrf_token": state.csrf_token,
            "filters_json": json.dumps(state.filters),
            "current_tconst": state.current_tconst,
            "current_ref_json": json.dumps(current_ref) if current_ref else None,
            "queue_json": json.dumps(state.queue),
            "prev_json": json.dumps(state.prev),
            "future_json": json.dumps(state.future),
            "seen_json": json.dumps(state.seen),
        }

    async def _insert_state(self, state: NavigationState) -> None:
        serialized = self._serialized_state_fields(state)
        await self.db_pool.execute(
            """
            INSERT INTO user_navigation_state (
                session_id, version, csrf_token, filters_json, current_tconst, current_ref_json,
                queue_json, prev_json, future_json, seen_json,
                created_at, last_activity_at, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                state.session_id,
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
        if dual_write and legacy_session and (
            legacy_session.get(CURRENT_MOVIE_KEY)
            or legacy_session.get(WATCH_QUEUE_KEY)
            or legacy_session.get(PREVIOUS_STACK_KEY)
            or legacy_session.get(FUTURE_STACK_KEY)
            or legacy_session.get(CRITERIA_KEY)
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
            self._serialized_state_fields(previous_state)
            if previous_state is not None
            else None
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
        params.extend([state.last_activity_at, state.expires_at, state.session_id, expected_version])
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
        return True

    def _write_legacy_session(self, state: NavigationState, legacy_session: MutableMapping[str, Any]) -> None:
        self.migration.write_legacy_session(state, legacy_session, criteria_from_filters)

    async def mutate(
        self,
        session_id: str,
        mutator: Callable[[NavigationState], Any | Awaitable[Any]],
        legacy_session: MutableMapping[str, Any] | None = None,
        current_state: NavigationState | None = None,
    ) -> MutationResult:
        from infra.metrics import navigation_state_conflicts_total

        for _ in range(2):
            current = current_state.clone() if current_state is not None else await self.get_state(session_id)
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

        return MutationResult(state=await self.get_state(session_id), conflicted=True)

    async def delete_state(self, session_id: str, legacy_session: MutableMapping[str, Any] | None = None) -> None:
        await self.db_pool.execute(
            "DELETE FROM user_navigation_state WHERE session_id = %s",
            [session_id],
            fetch="none",
        )
        if legacy_session is not None:
            legacy_session.clear()
