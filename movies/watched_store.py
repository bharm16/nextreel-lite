"""CRUD operations for the user_watched_movies table."""

from __future__ import annotations

from typing import Any

from infra.time_utils import utcnow
from logging_config import get_logger

logger = get_logger(__name__)

# Short TTL: stale-ok for the navigation hot path; invalidated on add/remove.
_WATCHED_CACHE_TTL = 300


class WatchedStore:
    """Data access layer for user watched-movie tracking."""

    def __init__(self, db_pool, cache=None):
        self.db_pool = db_pool
        self._cache = cache

    def _cache_key(self, user_id: str) -> str:
        return f"watched_tconsts:{user_id}"

    async def _invalidate_cache(self, user_id: str) -> None:
        if not self._cache:
            return
        try:
            from infra.cache import CacheNamespace

            await self._cache.delete(CacheNamespace.USER, self._cache_key(user_id))
        except Exception:  # pragma: no cover - defensive
            logger.debug("Watched cache invalidation failed for %s", user_id, exc_info=True)

    async def add(self, user_id: str, tconst: str) -> None:
        """Mark a movie as watched (idempotent)."""
        await self.db_pool.execute(
            """
            INSERT INTO user_watched_movies (user_id, tconst, watched_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE watched_at = VALUES(watched_at)
            """,
            [user_id, tconst, utcnow()],
            fetch="none",
        )
        await self._invalidate_cache(user_id)

    async def remove(self, user_id: str, tconst: str) -> None:
        """Remove a movie from the watched list."""
        await self.db_pool.execute(
            "DELETE FROM user_watched_movies WHERE user_id = %s AND tconst = %s",
            [user_id, tconst],
            fetch="none",
        )
        await self._invalidate_cache(user_id)

    async def is_watched(self, user_id: str, tconst: str) -> bool:
        """Check if a specific movie is in the user's watched list."""
        row = await self.db_pool.execute(
            "SELECT 1 AS cnt FROM user_watched_movies WHERE user_id = %s AND tconst = %s",
            [user_id, tconst],
            fetch="one",
        )
        return row is not None

    async def watched_tconsts(self, user_id: str) -> set[str]:
        """Return the set of all watched tconsts for a user.

        Cached in Redis under ``user:watched_tconsts:{user_id}`` with a 5-minute
        TTL. Invalidated on add()/remove(). Falls back to a direct DB read when
        no cache is configured or Redis is unavailable.
        """
        if self._cache:
            try:
                from infra.cache import CacheNamespace

                cached = await self._cache.get(CacheNamespace.USER, self._cache_key(user_id))
                if cached is not None:
                    return set(cached)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Watched cache read failed for %s", user_id, exc_info=True)

        rows = await self.db_pool.execute(
            "SELECT tconst FROM user_watched_movies WHERE user_id = %s",
            [user_id],
            fetch="all",
        )
        tconsts = {row["tconst"] for row in rows} if rows else set()

        if self._cache:
            try:
                from infra.cache import CacheNamespace

                await self._cache.set(
                    CacheNamespace.USER,
                    self._cache_key(user_id),
                    list(tconsts),
                    ttl=_WATCHED_CACHE_TTL,
                )
            except Exception:  # pragma: no cover - defensive
                logger.debug("Watched cache write failed for %s", user_id, exc_info=True)

        return tconsts

    async def count(self, user_id: str) -> int:
        """Return the count of watched movies for a user."""
        row = await self.db_pool.execute(
            "SELECT COUNT(*) AS cnt FROM user_watched_movies WHERE user_id = %s",
            [user_id],
            fetch="one",
        )
        return row["cnt"] if row else 0

    async def list_watched(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return watched movies with metadata, ordered by most recently watched."""
        rows = await self.db_pool.execute(
            """
            SELECT w.tconst, w.watched_at,
                   c.primaryTitle, c.startYear, c.genres, c.slug,
                   p.payload_json
            FROM user_watched_movies w
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            LEFT JOIN movie_projection p ON w.tconst = p.tconst
            WHERE w.user_id = %s
            ORDER BY w.watched_at DESC
            LIMIT %s OFFSET %s
            """,
            [user_id, limit, offset],
            fetch="all",
        )
        return rows if rows else []

    async def list_all_watched(
        self, user_id: str, limit: int = 5000
    ) -> list[dict[str, Any]]:
        """Return all watched movies for a user, ordered by most recently watched.

        Prefer ``list_watched(limit=, offset=)`` for paginated views; this method
        is retained for callers that genuinely need the full list.
        """
        return await self.list_watched(user_id, limit=limit, offset=0)
