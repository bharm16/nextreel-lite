"""CRUD operations for the user_watched_movies table."""

from __future__ import annotations

from typing import Any

from infra.time_utils import utcnow
from logging_config import get_logger

logger = get_logger(__name__)

# Short TTL: stale-ok for the navigation hot path; invalidated on add/remove.
_WATCHED_CACHE_TTL = 300

_SORT_MAP = {
    "recent": "w.watched_at DESC",
    "title_asc": "c.primaryTitle ASC",
    "title_desc": "c.primaryTitle DESC",
    "year_desc": "c.startYear DESC, c.primaryTitle ASC",
    "rating_desc": "c.averageRating DESC, c.primaryTitle ASC",
}


class WatchedStore:
    """Data access layer for user watched-movie tracking."""

    def __init__(self, db_pool, cache=None):
        self.db_pool = db_pool
        self._cache = cache

    def attach_cache(self, cache) -> None:
        """Attach a cache manager. Idempotent — callers may rebind."""
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

    async def add_bulk(self, user_id: str, tconsts: list[str]) -> int:
        """Mark multiple movies as watched in a single bulk insert.

        Uses multi-value INSERT with ON DUPLICATE KEY for idempotency.
        Processes in chunks of 500 to avoid query size limits.

        Returns:
            Number of tconsts processed (not necessarily newly inserted).
        """
        if not tconsts:
            return 0

        now = utcnow()
        chunk_size = 500
        total = 0

        for i in range(0, len(tconsts), chunk_size):
            chunk = tconsts[i : i + chunk_size]
            placeholders = ", ".join(["(%s, %s, %s)"] * len(chunk))
            params = []
            for tc in chunk:
                params.extend([user_id, tc, now])

            await self.db_pool.execute(
                "INSERT INTO user_watched_movies (user_id, tconst, watched_at) "
                "VALUES " + placeholders + " "
                "ON DUPLICATE KEY UPDATE watched_at = VALUES(watched_at)",
                params,
                fetch="none",
            )
            total += len(chunk)

        await self._invalidate_cache(user_id)
        logger.info("Bulk-added %d watched movies for user %s", total, user_id)
        return total

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

        async def _loader() -> list[str]:
            rows = await self.db_pool.execute(
                "SELECT tconst FROM user_watched_movies WHERE user_id = %s",
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
            ttl=_WATCHED_CACHE_TTL,
        )
        return set(cached) if cached is not None else set()

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
            SELECT sub.tconst, sub.watched_at,
                   sub.primaryTitle, sub.startYear, sub.genres, sub.slug,
                   p.payload_json
            FROM (
                SELECT w.tconst, w.watched_at,
                       c.primaryTitle, c.startYear, c.genres, c.slug
                FROM user_watched_movies w
                LEFT JOIN movie_candidates c ON w.tconst = c.tconst
                WHERE w.user_id = %s
                ORDER BY w.watched_at DESC, c.startYear DESC, c.primaryTitle ASC
                LIMIT %s OFFSET %s
            ) sub
            LEFT JOIN movie_projection p ON sub.tconst = p.tconst
            """,
            [user_id, limit, offset],
            fetch="all",
        )
        return rows if rows else []

    async def list_watched_enriched(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return watched movies that have READY projections, ordered by most recently watched."""
        rows = await self.db_pool.execute(
            """
            SELECT sub.tconst, sub.watched_at,
                   sub.primaryTitle, sub.startYear, sub.genres, sub.slug,
                   p.payload_json
            FROM (
                SELECT w.tconst, w.watched_at,
                       c.primaryTitle, c.startYear, c.genres, c.slug
                FROM user_watched_movies w
                INNER JOIN movie_projection p2 ON w.tconst = p2.tconst
                LEFT JOIN movie_candidates c ON w.tconst = c.tconst
                WHERE w.user_id = %s AND p2.projection_state = %s
                ORDER BY w.watched_at DESC, c.startYear DESC, c.primaryTitle ASC
                LIMIT %s OFFSET %s
            ) sub
            INNER JOIN movie_projection p ON sub.tconst = p.tconst
            """,
            [user_id, "ready", limit, offset],
            fetch="all",
        )
        return rows if rows else []

    async def count_enriched(self, user_id: str) -> int:
        """Return count of watched movies with READY projections."""
        row = await self.db_pool.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM user_watched_movies w
            INNER JOIN movie_projection p ON w.tconst = p.tconst
            WHERE w.user_id = %s AND p.projection_state = %s
            """,
            [user_id, "ready"],
            fetch="one",
        )
        return row["cnt"] if row else 0

    async def list_watched_filtered(
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
        """Return watched movies with optional filtering and sorting.

        Filters combine as AND across categories, OR within a category.
        """
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
            SELECT sub.tconst, sub.watched_at,
                   sub.primaryTitle, sub.startYear, sub.genres, sub.slug,
                   sub.averageRating,
                   p.payload_json
            FROM (
                SELECT w.tconst, w.watched_at,
                       c.primaryTitle, c.startYear, c.genres, c.slug,
                       c.averageRating
                FROM user_watched_movies w
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
        """Return count of watched movies matching the given filters."""
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
            FROM user_watched_movies w
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            WHERE {where_sql}
            """,
            params,
            fetch="one",
        )
        return row["cnt"] if row else 0

    async def available_filter_chips(self, user_id: str) -> dict[str, list]:
        """Return available filter chip options based on the user's watched data."""
        rows = await self.db_pool.execute(
            """
            SELECT c.startYear, c.genres, c.averageRating
            FROM user_watched_movies w
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
            rating_tiers.append({"label": "6\u20138", "min": 6.0, "max": 7.99})
        if has_under_6:
            rating_tiers.append({"label": "<6", "min": 0.0, "max": 5.99})

        return {
            "decades": sorted(decade_set, reverse=True),
            "genres": sorted(genre_set),
            "ratings": rating_tiers,
        }

    async def ready_tconsts_for_import(self, tconsts: list[str]) -> set[str]:
        """Return imported tconsts that already have READY projections."""
        if not tconsts:
            return set()
        unique = list(dict.fromkeys(tconsts))
        placeholders = ", ".join(["%s"] * len(unique))
        rows = await self.db_pool.execute(
            "SELECT tconst FROM movie_projection "
            "WHERE tconst IN (" + placeholders + ") "
            "AND projection_state = %s",
            [*unique, "ready"],
            fetch="all",
        )
        return {row["tconst"] for row in rows} if rows else set()

    async def ready_import_rows(self, user_id: str, tconsts: list[str]) -> list[dict[str, Any]]:
        """Return watched rows for imported tconsts whose projections are READY."""
        if not tconsts:
            return []
        unique = list(dict.fromkeys(tconsts))
        placeholders = ", ".join(["%s"] * len(unique))
        rows = await self.db_pool.execute(
            "SELECT w.tconst, w.watched_at, "
            "c.primaryTitle, c.startYear, c.genres, c.slug, "
            "p.payload_json "
            "FROM user_watched_movies w "
            "INNER JOIN movie_projection p ON w.tconst = p.tconst "
            "LEFT JOIN movie_candidates c ON w.tconst = c.tconst "
            "WHERE w.tconst IN (" + placeholders + ") "
            "AND w.user_id = %s "
            "AND p.projection_state = %s "
            "ORDER BY w.watched_at DESC",
            [*unique, user_id, "ready"],
            fetch="all",
        )
        return rows if rows else []
