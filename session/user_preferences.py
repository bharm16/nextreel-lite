from __future__ import annotations

import json

from infra.time_utils import utcnow

_VALID_THEMES = frozenset({"light", "dark", "system"})


async def get_exclude_watched_default(db_pool, user_id: str) -> bool:
    row = await db_pool.execute(
        "SELECT exclude_watched_default FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )
    if not row:
        return True
    return bool(row.get("exclude_watched_default", True))


async def set_exclude_watched_default(db_pool, user_id: str, value: bool) -> None:
    await db_pool.execute(
        """
        UPDATE users
        SET exclude_watched_default = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [bool(value), utcnow(), user_id],
        fetch="none",
    )


async def get_exclude_watchlist_default(db_pool, user_id: str) -> bool:
    row = await db_pool.execute(
        "SELECT exclude_watchlist_default FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )
    if not row:
        return True
    return bool(row.get("exclude_watchlist_default", True))


async def set_exclude_watchlist_default(db_pool, user_id: str, value: bool) -> None:
    await db_pool.execute(
        """
        UPDATE users
        SET exclude_watchlist_default = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [bool(value), utcnow(), user_id],
        fetch="none",
    )


async def get_theme_preference(db_pool, user_id: str) -> str | None:
    row = await db_pool.execute(
        "SELECT theme_preference FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )
    if not row:
        return None
    value = row.get("theme_preference")
    return value if value in _VALID_THEMES else None


async def set_theme_preference(db_pool, user_id: str, value: str | None) -> None:
    if value is not None and value not in _VALID_THEMES:
        raise ValueError(f"Invalid theme preference: {value!r}")
    await db_pool.execute(
        """
        UPDATE users
        SET theme_preference = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [value, utcnow(), user_id],
        fetch="none",
    )


async def get_default_filters(db_pool, user_id: str) -> dict | None:
    row = await db_pool.execute(
        "SELECT default_filters_json FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )
    if not row or not row.get("default_filters_json"):
        return None
    raw = row["default_filters_json"]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (list, type(None))):
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def set_default_filters(db_pool, user_id: str, filters: dict) -> None:
    await db_pool.execute(
        """
        UPDATE users
        SET default_filters_json = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [json.dumps(filters), utcnow(), user_id],
        fetch="none",
    )


async def clear_default_filters(db_pool, user_id: str) -> None:
    await db_pool.execute(
        """
        UPDATE users
        SET default_filters_json = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [None, utcnow(), user_id],
        fetch="none",
    )
