"""CRUD operations for the user_watchlist table."""

from __future__ import annotations

from typing import Any

from infra.cache import USER_LIST_CACHE_TTL_SECONDS
from infra.time_utils import utcnow
from logging_config import get_logger

logger = get_logger(__name__)

_SORT_MAP = {
    "recent": "w.added_at DESC",
    "title_asc": "c.primaryTitle ASC",
    "title_desc": "c.primaryTitle DESC",
    "year_desc": "c.startYear DESC, c.primaryTitle ASC",
    "rating_desc": "c.averageRating DESC, c.primaryTitle ASC",
}


class WatchlistStore:
    """Data access layer for user watchlist (save-for-later) tracking."""

    def __init__(self, db_pool, cache=None):
        self.db_pool = db_pool
        self._cache = cache

    def attach_cache(self, cache) -> None:
        """Attach a cache manager. Idempotent — callers may rebind."""
        self._cache = cache

    def _cache_key(self, user_id: str) -> str:
        return f"watchlist_tconsts:{user_id}"

    async def _invalidate_cache(self, user_id: str) -> None:
        if not self._cache:
            return
        try:
            from infra.cache import CacheNamespace

            await self._cache.delete(CacheNamespace.USER, self._cache_key(user_id))
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "Watchlist cache invalidation failed for %s", user_id, exc_info=True
            )

    async def add(self, user_id: str, tconst: str) -> None:
        """Add a movie to the watchlist (idempotent)."""
        await self.db_pool.execute(
            """
            INSERT INTO user_watchlist (user_id, tconst, added_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE added_at = VALUES(added_at)
            """,
            [user_id, tconst, utcnow()],
            fetch="none",
        )
        await self._invalidate_cache(user_id)

    async def remove(self, user_id: str, tconst: str) -> None:
        """Remove a movie from the watchlist."""
        await self.db_pool.execute(
            "DELETE FROM user_watchlist WHERE user_id = %s AND tconst = %s",
            [user_id, tconst],
            fetch="none",
        )
        await self._invalidate_cache(user_id)

    async def is_in_watchlist(self, user_id: str, tconst: str) -> bool:
        row = await self.db_pool.execute(
            "SELECT 1 AS cnt FROM user_watchlist WHERE user_id = %s AND tconst = %s",
            [user_id, tconst],
            fetch="one",
        )
        return row is not None

    async def watchlist_tconsts(self, user_id: str) -> set[str]:
        """Return the set of all watchlist tconsts for a user.

        Cached in Redis under ``user:watchlist_tconsts:{user_id}`` with a
        5-minute TTL. Invalidated on add()/remove(). Falls back to a direct
        DB read when no cache is configured or Redis is unavailable.
        """

        async def _loader() -> list[str]:
            rows = await self.db_pool.execute(
                "SELECT tconst FROM user_watchlist WHERE user_id = %s",
                [user_id],
                fetch="all",
            )
            return [row["tconst"] for row in rows] if rows else []

        if not self._cache:
            return set(await _loader())

        from infra.cache import CacheNamespace

        cached = await self._cache.safe_get_or_set(
            CacheNamespace.USER,
            self._cache_key(user_id),
            _loader,
            ttl=USER_LIST_CACHE_TTL_SECONDS,
        )
        return set(cached) if cached is not None else set()

    async def count(self, user_id: str) -> int:
        row = await self.db_pool.execute(
            "SELECT COUNT(*) AS cnt FROM user_watchlist WHERE user_id = %s",
            [user_id],
            fetch="one",
        )
        return row["cnt"] if row else 0

    async def list_watchlist_filtered(
        self,
        user_id: str,
        *,
        sort: str = "recent",
        limit: int = 60,
        offset: int = 0,
        decades: list[str] | None = None,
        rating_min: float | None = None,
        rating_max: float | None = None,
        genres: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return watchlist movies with optional filtering and sorting."""
        where_clauses = ["w.user_id = %s"]
        params: list[Any] = [user_id]

        if decades:
            decade_parts = []
            for decade_str in decades:
                try:
                    decade_start = int(decade_str)
                except (TypeError, ValueError):
                    continue
                decade_parts.append("(c.startYear >= %s AND c.startYear <= %s)")
                params.extend([decade_start, decade_start + 9])
            if decade_parts:
                where_clauses.append("(" + " OR ".join(decade_parts) + ")")

        if rating_min is not None:
            where_clauses.append("c.averageRating >= %s")
            params.append(rating_min)
        if rating_max is not None:
            where_clauses.append("c.averageRating <= %s")
            params.append(rating_max)

        if genres:
            genre_parts = []
            for genre in genres:
                genre_parts.append("FIND_IN_SET(%s, c.genres) > 0")
                params.append(genre)
            if genre_parts:
                where_clauses.append("(" + " OR ".join(genre_parts) + ")")

        order_by = _SORT_MAP.get(sort, _SORT_MAP["recent"])
        where_sql = " AND ".join(where_clauses)
        params.extend([limit, offset])

        rows = await self.db_pool.execute(
            f"""
            SELECT sub.tconst, sub.added_at,
                   sub.primaryTitle, sub.startYear, sub.genres, sub.slug,
                   sub.averageRating,
                   p.payload_json, p.public_id
            FROM (
                SELECT w.tconst, w.added_at,
                       c.primaryTitle, c.startYear, c.genres, c.slug,
                       c.averageRating
                FROM user_watchlist w
                LEFT JOIN movie_candidates c ON w.tconst = c.tconst
                WHERE {where_sql}
                ORDER BY {order_by}
                LIMIT %s OFFSET %s
            ) sub
            LEFT JOIN movie_projection p ON sub.tconst = p.tconst
            """,
            params,
            fetch="all",
        )
        return rows if rows else []

    async def count_filtered(
        self,
        user_id: str,
        *,
        decades: list[str] | None = None,
        rating_min: float | None = None,
        rating_max: float | None = None,
        genres: list[str] | None = None,
    ) -> int:
        where_clauses = ["w.user_id = %s"]
        params: list[Any] = [user_id]

        if decades:
            decade_parts = []
            for decade_str in decades:
                try:
                    decade_start = int(decade_str)
                except (TypeError, ValueError):
                    continue
                decade_parts.append("(c.startYear >= %s AND c.startYear <= %s)")
                params.extend([decade_start, decade_start + 9])
            if decade_parts:
                where_clauses.append("(" + " OR ".join(decade_parts) + ")")

        if rating_min is not None:
            where_clauses.append("c.averageRating >= %s")
            params.append(rating_min)
        if rating_max is not None:
            where_clauses.append("c.averageRating <= %s")
            params.append(rating_max)

        if genres:
            genre_parts = []
            for genre in genres:
                genre_parts.append("FIND_IN_SET(%s, c.genres) > 0")
                params.append(genre)
            if genre_parts:
                where_clauses.append("(" + " OR ".join(genre_parts) + ")")

        where_sql = " AND ".join(where_clauses)

        row = await self.db_pool.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM user_watchlist w
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            WHERE {where_sql}
            """,
            params,
            fetch="one",
        )
        return row["cnt"] if row else 0

    async def available_filter_chips(self, user_id: str) -> dict[str, list]:
        rows = await self.db_pool.execute(
            """
            SELECT c.startYear, c.genres, c.averageRating
            FROM user_watchlist w
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            WHERE w.user_id = %s AND c.tconst IS NOT NULL
            """,
            [user_id],
            fetch="all",
        )
        if not rows:
            return {"decades": [], "genres": [], "ratings": []}

        decade_set: set[str] = set()
        genre_set: set[str] = set()
        has_8_plus = False
        has_6_8 = False
        has_under_6 = False

        for row in rows:
            year = row.get("startYear")
            if year:
                try:
                    decade = (int(year) // 10) * 10
                    decade_set.add(f"{decade}s")
                except (TypeError, ValueError):
                    pass

            genres_csv = row.get("genres")
            if genres_csv and isinstance(genres_csv, str):
                for g in genres_csv.split(","):
                    g = g.strip()
                    if g:
                        genre_set.add(g)

            rating = row.get("averageRating")
            if rating is not None:
                try:
                    r = float(rating)
                    if r >= 8.0:
                        has_8_plus = True
                    elif r >= 6.0:
                        has_6_8 = True
                    else:
                        has_under_6 = True
                except (TypeError, ValueError):
                    pass

        rating_tiers = []
        if has_8_plus:
            rating_tiers.append({"label": "8+", "min": 8.0, "max": 10.0})
        if has_6_8:
            rating_tiers.append({"label": "6–8", "min": 6.0, "max": 7.99})
        if has_under_6:
            rating_tiers.append({"label": "<6", "min": 0.0, "max": 5.99})

        return {
            "decades": sorted(decade_set, reverse=True),
            "genres": sorted(genre_set),
            "ratings": rating_tiers,
        }
