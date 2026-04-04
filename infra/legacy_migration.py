"""Legacy Redis → MySQL session migration helper.

This module encapsulates the dual-write migration logic for transitioning
navigation state from Redis-backed sessions to MySQL-backed state.  It is
designed to be removable once the migration window closes:

    1. Set ``NAV_STATE_DUAL_WRITE_ENABLED=false`` in production.
    2. Monitor that ``navigation_state_redis_import_total`` stays at zero.
    3. Delete this file and remove the ``migration`` attribute from
       ``NavigationStateStore.__init__``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any, MutableMapping

from infra.time_utils import utcnow
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

logger = get_logger(__name__)

MIGRATION_META_STARTED_AT = "nav_state_migration_started_at"
MIGRATION_META_LAST_IMPORT_AT = "nav_state_last_redis_import_at"


def _migration_min_days() -> int:
    return int(os.getenv("NAV_STATE_MIGRATION_MIN_DAYS", 7))


def _migration_quiet_hours() -> int:
    return int(os.getenv("NAV_STATE_ZERO_IMPORT_HOURS", 24))


class LegacyMigrationHelper:
    """Handles dual-write and Redis→MySQL session import during migration."""

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

    def state_from_legacy(
        self,
        legacy_session: MutableMapping[str, Any],
        fresh_state_fn,
        filters_from_criteria_fn,
        normalize_ref_fn,
        normalize_ref_list_fn,
        normalize_seen_fn,
        *,
        queue_target: int,
        prev_max: int,
        future_max: int,
    ):
        """Convert a legacy Redis session dict to a NavigationState.

        Accepts helper functions from the navigation_state module to
        avoid circular imports.
        """
        raw_filters = legacy_session.get(CURRENT_FILTERS_KEY)
        if not isinstance(raw_filters, dict):
            raw_filters = filters_from_criteria_fn(legacy_session.get(CRITERIA_KEY, {}))
        elif "genres_selected" not in raw_filters and "genres[]" in raw_filters:
            genres = raw_filters.get("genres[]")
            if isinstance(genres, list):
                raw_filters["genres_selected"] = genres
            elif genres:
                raw_filters["genres_selected"] = [genres]
            raw_filters.pop("genres[]", None)

        from infra.navigation_state import default_filter_state

        current_movie = legacy_session.get(CURRENT_MOVIE_KEY)
        current_ref = normalize_ref_fn(current_movie)
        state = fresh_state_fn(uuid.uuid4().hex)
        state.csrf_token = legacy_session.get("_csrf_token") or state.csrf_token
        state.filters = raw_filters or default_filter_state()
        state.current_tconst = current_ref["tconst"] if current_ref else None
        state.current_ref = current_ref
        state.queue = normalize_ref_list_fn(legacy_session.get(WATCH_QUEUE_KEY, []), max_items=queue_target)
        state.prev = normalize_ref_list_fn(legacy_session.get(PREVIOUS_STACK_KEY, []), max_items=prev_max)
        state.future = normalize_ref_list_fn(legacy_session.get(FUTURE_STACK_KEY, []), max_items=future_max)
        state.seen = normalize_seen_fn(legacy_session.get(SEEN_TCONSTS_KEY, []))
        return state

    def write_legacy_session(
        self,
        state,
        legacy_session: MutableMapping[str, Any],
        criteria_from_filters_fn,
    ) -> None:
        """Write NavigationState back to a legacy Redis session dict."""
        legacy_session["_csrf_token"] = state.csrf_token
        legacy_session[CURRENT_FILTERS_KEY] = state.filters
        legacy_session[CRITERIA_KEY] = criteria_from_filters_fn(state.filters)
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
            {
                "imdb_id": state.current_tconst,
                "title": (state.current_ref or {}).get("title"),
                "slug": (state.current_ref or {}).get("slug"),
            }
            if state.current_tconst
            else None
        )
