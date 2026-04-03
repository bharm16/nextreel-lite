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
from movies.filter_parser import VALID_GENRES, extract_movie_filter_criteria
from session.keys import (
    CRITERIA_KEY,
    CURRENT_FILTERS_KEY,
    CURRENT_MOVIE_KEY,
    FUTURE_STACK_KEY,
    PREVIOUS_STACK_KEY,
    SEEN_TCONSTS_KEY,
    WATCH_QUEUE_KEY,
)

logger = get_logger(__name__)

SESSION_COOKIE_NAME = "nr_sid"
SESSION_COOKIE_MAX_AGE = 8 * 60 * 60
QUEUE_TARGET = 5
QUEUE_REFILL_THRESHOLD = 2
PREV_STACK_MAX = 20
FUTURE_STACK_MAX = 20
SEEN_MAX = 50
MAX_FILTER_VALUE_LEN = 64
MIGRATION_META_STARTED_AT = "nav_state_migration_started_at"
MIGRATION_META_LAST_IMPORT_AT = "nav_state_last_redis_import_at"


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (for MySQL compat)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _idle_timeout() -> timedelta:
    return timedelta(minutes=int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", 15)))


def _max_duration() -> timedelta:
    return timedelta(hours=int(os.getenv("MAX_SESSION_DURATION_HOURS", 8)))


def _migration_min_days() -> int:
    return int(os.getenv("NAV_STATE_MIGRATION_MIN_DAYS", 7))


def _migration_quiet_hours() -> int:
    return int(os.getenv("NAV_STATE_ZERO_IMPORT_HOURS", 24))


def default_filter_state(current_year: int | None = None) -> dict[str, Any]:
    year = current_year or utcnow().year
    return {
        "year_min": 1900,
        "year_max": year,
        "imdb_score_min": 7.0,
        "imdb_score_max": 10.0,
        "num_votes_min": 100000,
        "num_votes_max": 200000,
        "language": "en",
        "genres_selected": [],
    }


def filters_from_criteria(criteria: dict[str, Any]) -> dict[str, Any]:
    filters = default_filter_state()
    if "min_year" in criteria:
        filters["year_min"] = criteria["min_year"]
    if "max_year" in criteria:
        filters["year_max"] = criteria["max_year"]
    if "min_rating" in criteria:
        filters["imdb_score_min"] = criteria["min_rating"]
    if "max_rating" in criteria:
        filters["imdb_score_max"] = criteria["max_rating"]
    if "min_votes" in criteria:
        filters["num_votes_min"] = criteria["min_votes"]
    if "max_votes" in criteria:
        filters["num_votes_max"] = criteria["max_votes"]
    if "language" in criteria:
        filters["language"] = criteria["language"]
    if criteria.get("genres"):
        filters["genres_selected"] = list(criteria["genres"])
    return filters


class _StoredFilterForm:
    def __init__(self, filters: dict[str, Any]):
        self._filters = filters

    def get(self, key: str, default: Any = None) -> Any:
        if key == "genres[]":
            genres = self._filters.get("genres_selected")
            return genres[0] if genres else default
        return self._filters.get(key, default)

    def getlist(self, key: str) -> list[Any]:
        if key == "genres[]":
            return list(self._filters.get("genres_selected", []))
        value = self._filters.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        return [value]


def criteria_from_filters(filters: dict[str, Any]) -> dict[str, Any]:
    merged = default_filter_state()
    merged.update(filters or {})
    return extract_movie_filter_criteria(_StoredFilterForm(merged))


def normalize_filters(form_data) -> dict[str, Any]:
    filters = default_filter_state()
    scalar_keys = (
        "year_min",
        "year_max",
        "imdb_score_min",
        "imdb_score_max",
        "num_votes_min",
        "num_votes_max",
        "language",
    )
    for key in scalar_keys:
        value = form_data.get(key)
        if isinstance(value, str):
            filters[key] = value[:MAX_FILTER_VALUE_LEN]
        elif value is not None:
            filters[key] = value

    raw_genres = form_data.getlist("genres[]")
    filters["genres_selected"] = [
        genre[:MAX_FILTER_VALUE_LEN]
        for genre in raw_genres
        if isinstance(genre, str) and genre in VALID_GENRES
    ]
    return filters


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
        )


@dataclass
class MutationResult:
    state: NavigationState | None
    result: Any = None
    conflicted: bool = False


class NavigationStateStore:
    def __init__(self, db_pool):
        self.db_pool = db_pool

    async def _select_meta(self, key: str) -> str | None:
        row = await self.db_pool.execute(
            "SELECT meta_value FROM runtime_metadata WHERE meta_key = %s",
            [key],
            fetch="one",
        )
        return row["meta_value"] if row else None

    async def _set_meta(self, key: str, value: str) -> None:
        now = utcnow()
        await self.db_pool.execute(
            """
            INSERT INTO runtime_metadata (meta_key, meta_value, updated_at)
            VALUES (%s, %s, %s)
            AS new_row
            ON DUPLICATE KEY UPDATE
                meta_value = new_row.meta_value,
                updated_at = new_row.updated_at
            """,
            [key, value, now],
            fetch="none",
        )

    async def _ensure_migration_started_at(self) -> datetime:
        existing = await self._select_meta(MIGRATION_META_STARTED_AT)
        if existing:
            return datetime.fromisoformat(existing)
        now = utcnow()
        await self._set_meta(MIGRATION_META_STARTED_AT, now.isoformat())
        return now

    async def dual_write_enabled(self) -> bool:
        if os.getenv("NAV_STATE_DUAL_WRITE_ENABLED", "true").lower() == "false":
            return False

        started_at = await self._ensure_migration_started_at()
        if utcnow() < started_at + timedelta(days=_migration_min_days()):
            return True

        last_import = await self._select_meta(MIGRATION_META_LAST_IMPORT_AT)
        if not last_import:
            return False

        last_import_at = datetime.fromisoformat(last_import)
        return utcnow() < last_import_at + timedelta(hours=_migration_quiet_hours())

    async def record_legacy_import(self) -> None:
        from infra.metrics import navigation_state_redis_import_total

        await self._set_meta(MIGRATION_META_LAST_IMPORT_AT, utcnow().isoformat())
        navigation_state_redis_import_total.inc()

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
        )

    def _state_from_legacy(self, session_id: str, legacy_session: MutableMapping[str, Any]) -> NavigationState:
        raw_filters = legacy_session.get(CURRENT_FILTERS_KEY)
        if not isinstance(raw_filters, dict):
            raw_filters = filters_from_criteria(legacy_session.get(CRITERIA_KEY, {}))
        elif "genres_selected" not in raw_filters and "genres[]" in raw_filters:
            genres = raw_filters.get("genres[]")
            if isinstance(genres, list):
                raw_filters["genres_selected"] = genres
            elif genres:
                raw_filters["genres_selected"] = [genres]
            raw_filters.pop("genres[]", None)

        current_movie = legacy_session.get(CURRENT_MOVIE_KEY)
        current_ref = _normalize_ref(current_movie)
        state = self._fresh_state(session_id)
        state.csrf_token = legacy_session.get("_csrf_token") or state.csrf_token
        state.filters = raw_filters or default_filter_state()
        state.current_tconst = current_ref["tconst"] if current_ref else None
        state.queue = _normalize_ref_list(legacy_session.get(WATCH_QUEUE_KEY, []), max_items=QUEUE_TARGET)
        state.prev = _normalize_ref_list(legacy_session.get(PREVIOUS_STACK_KEY, []), max_items=PREV_STACK_MAX)
        state.future = _normalize_ref_list(legacy_session.get(FUTURE_STACK_KEY, []), max_items=FUTURE_STACK_MAX)
        state.seen = _normalize_seen(legacy_session.get(SEEN_TCONSTS_KEY, []))
        return state

    async def _load_row(self, session_id: str) -> NavigationState | None:
        row = await self.db_pool.execute(
            """
            SELECT session_id, version, csrf_token, filters_json, current_tconst,
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
        )

    async def _insert_state(self, state: NavigationState) -> None:
        await self.db_pool.execute(
            """
            INSERT INTO user_navigation_state (
                session_id, version, csrf_token, filters_json, current_tconst,
                queue_json, prev_json, future_json, seen_json,
                created_at, last_activity_at, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                state.session_id,
                state.version,
                state.csrf_token,
                json.dumps(state.filters),
                state.current_tconst,
                json.dumps(state.queue),
                json.dumps(state.prev),
                json.dumps(state.future),
                json.dumps(state.seen),
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
        probe_id = f"ready-{uuid.uuid4().hex}"
        state = self._fresh_state(probe_id)
        await self._insert_state(state)
        fetched = await self.get_state(probe_id)
        await self.db_pool.execute(
            "DELETE FROM user_navigation_state WHERE session_id = %s",
            [probe_id],
            fetch="none",
        )
        return fetched is not None

    async def save_state(self, state: NavigationState, expected_version: int) -> bool:
        now = utcnow()
        state.last_activity_at = now
        state.expires_at = self._fresh_expiry(state.created_at, now)
        next_version = expected_version + 1
        updated = await self.db_pool.execute(
            """
            UPDATE user_navigation_state
            SET version = %s,
                csrf_token = %s,
                filters_json = %s,
                current_tconst = %s,
                queue_json = %s,
                prev_json = %s,
                future_json = %s,
                seen_json = %s,
                last_activity_at = %s,
                expires_at = %s
            WHERE session_id = %s AND version = %s
            """,
            [
                next_version,
                state.csrf_token,
                json.dumps(state.filters),
                state.current_tconst,
                json.dumps(state.queue),
                json.dumps(state.prev),
                json.dumps(state.future),
                json.dumps(state.seen),
                state.last_activity_at,
                state.expires_at,
                state.session_id,
                expected_version,
            ],
            fetch="none",
        )
        if updated != 1:
            return False
        state.version = next_version
        return True

    def _write_legacy_session(self, state: NavigationState, legacy_session: MutableMapping[str, Any]) -> None:
        legacy_session["_csrf_token"] = state.csrf_token
        legacy_session[CURRENT_FILTERS_KEY] = state.filters
        legacy_session[CRITERIA_KEY] = criteria_from_filters(state.filters)
        legacy_session[WATCH_QUEUE_KEY] = [
            {"imdb_id": ref["tconst"], "title": ref.get("title"), "slug": ref.get("slug")}
            for ref in state.queue
        ]
        legacy_session[PREVIOUS_STACK_KEY] = [
            {"imdb_id": ref["tconst"], "title": ref.get("title"), "slug": ref.get("slug")}
            for ref in state.prev
        ]
        legacy_session[FUTURE_STACK_KEY] = [
            {"imdb_id": ref["tconst"], "title": ref.get("title"), "slug": ref.get("slug")}
            for ref in state.future
        ]
        legacy_session[SEEN_TCONSTS_KEY] = list(state.seen)
        legacy_session[CURRENT_MOVIE_KEY] = (
            {"imdb_id": state.current_tconst} if state.current_tconst else None
        )

    async def mutate(
        self,
        session_id: str,
        mutator: Callable[[NavigationState], Any | Awaitable[Any]],
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> MutationResult:
        from infra.metrics import navigation_state_conflicts_total

        for _ in range(2):
            current = await self.get_state(session_id)
            if not current:
                return MutationResult(state=None, conflicted=True)

            working = current.clone()
            result = mutator(working)
            if inspect.isawaitable(result):
                result = await result

            if await self.save_state(working, expected_version=current.version):
                if legacy_session and await self.dual_write_enabled():
                    self._write_legacy_session(working, legacy_session)
                return MutationResult(state=working, result=result, conflicted=False)

            navigation_state_conflicts_total.inc()

        return MutationResult(state=await self.get_state(session_id), conflicted=True)

    async def delete_state(self, session_id: str, legacy_session: MutableMapping[str, Any] | None = None) -> None:
        await self.db_pool.execute(
            "DELETE FROM user_navigation_state WHERE session_id = %s",
            [session_id],
            fetch="none",
        )
        if legacy_session is not None:
            legacy_session.clear()
