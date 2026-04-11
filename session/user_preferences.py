from __future__ import annotations

from infra.time_utils import utcnow


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
