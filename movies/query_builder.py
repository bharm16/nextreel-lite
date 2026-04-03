from __future__ import annotations

import hashlib
import json
import random
import time
from datetime import datetime, timezone
from typing import Any

from logging_config import get_logger

from infra.cache import CacheNamespace
from infra.errors import DatabaseError
from .interfaces import MovieFetcher

logger = get_logger(__name__)


def _criteria_cache_key(criteria: dict[str, Any]) -> str:
    """Build a deterministic cache key from filter criteria."""
    blob = json.dumps(criteria, sort_keys=True, default=str).encode()
    return "count:" + hashlib.sha256(blob).hexdigest()[:16]


# Columns needed by downstream consumers (MovieNavigator only reads ``tconst``
# from the result rows, but the integration test in test_filter_backend.py
# asserts on startYear / averageRating / numVotes / titleType / language).
_CACHE_COLUMNS = "tconst, primaryTitle, startYear, genres, language, titleType, slug, averageRating, numVotes"

# Base WHERE clause template — shared by all three table paths.  The
# language predicate is appended dynamically by ``_where_clause`` so that
# ``language='any'`` omits it entirely (sargable), and specific languages
# avoid the ``%s = 'any'`` constant comparison that blocked index use.
_WHERE_BASE = (
    "WHERE {p}titleType = %s "
    "AND {p}startYear BETWEEN %s AND %s "
    "AND {r}averageRating BETWEEN %s AND %s "
    "AND {r}numVotes >= %s AND {r}numVotes <= %s"
)

_LANG_CLAUSE = " AND ({p}language = %s OR {p}language LIKE %s OR {p}language IS NULL)"


class MovieQueryBuilder:
    """Build optimized SQL queries using new indexes and cache tables.

    All query variants share the same WHERE clause structure.  Parameter
    count depends on the language filter: 7 base params when
    ``language='any'``, 9 when a specific language is requested.
    """

    @staticmethod
    def should_use_cache(criteria: dict[str, Any], current_year: int | None = None) -> bool:
        """Determine if we should use the cache table based on criteria."""
        if current_year is None:
            current_year = datetime.now(timezone.utc).year
        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", current_year)
        min_votes = criteria.get("min_votes", 100000)

        # Use cache for movies 1980–present with significant votes
        if min_year >= 1980 and max_year <= current_year and min_votes >= 10000:
            return True
        return False

    @staticmethod
    def should_use_recent_cache(criteria: dict[str, Any], current_year: int | None = None) -> bool:
        """Determine if we should use the recent movies cache.

        The threshold is rolling: movies from the last 2 full calendar years
        are considered "recent" and routed to the lighter cache table to avoid
        13+ second full-table queries.
        """
        if current_year is None:
            current_year = datetime.now(timezone.utc).year
        recent_threshold = current_year - 2  # e.g. 2024 when year is 2026

        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", current_year)

        # Only route to the recent cache when the entire requested year range
        # falls within that cache's coverage window. Broad queries like
        # 1900-current_year must not be truncated to recent releases.
        return min_year >= recent_threshold and max_year >= recent_threshold

    @staticmethod
    def _where_clause(
        use_cache: bool = False,
        use_recent: bool = False,
        language: str = "any",
    ) -> str:
        """Return the shared WHERE clause with correct table alias prefixes.

        When *language* is ``"any"`` the language predicate is omitted entirely,
        letting MySQL use available indexes without a non-sargable OR chain.
        """
        if use_cache or use_recent:
            p, r = "", ""
        else:
            p, r = "tb.", "tr."
        base = _WHERE_BASE.format(p=p, r=r)
        if language != "any":
            base += _LANG_CLAUSE.format(p=p)
        return base

    @staticmethod
    def build_base_query(
        use_cache: bool = False,
        use_recent: bool = False,
        language: str = "any",
    ) -> str:
        """Build base SELECT query for the appropriate table."""
        where = MovieQueryBuilder._where_clause(use_cache, use_recent, language)
        if use_recent:
            return "SELECT " + _CACHE_COLUMNS + " FROM recent_movies_cache " + where
        elif use_cache:
            return "SELECT " + _CACHE_COLUMNS + " FROM popular_movies_cache " + where
        else:
            return (
                "SELECT tb.tconst, tb.primaryTitle, tb.startYear, tb.genres, "
                "tb.language, tb.titleType, tb.slug, tr.averageRating, tr.numVotes "
                "FROM `title.basics` tb "
                "JOIN `title.ratings` tr ON tb.tconst = tr.tconst "
                + where
            )

    @staticmethod
    def build_parameters(criteria: dict[str, Any], current_year: int | None = None) -> list[Any]:
        """Build parameters matching the WHERE clause from ``_where_clause``.

        Returns 7 base params when ``language='any'`` (no language predicate),
        or 9 params when a specific language is requested.
        """
        if current_year is None:
            current_year = datetime.now(timezone.utc).year
        lang = criteria.get("language", "en")

        params: list[Any] = [
            criteria.get("title_type", "movie"),
            criteria.get("min_year", 1900),
            criteria.get("max_year", current_year),
            criteria.get("min_rating", 7.0),
            criteria.get("max_rating", 10),
            criteria.get("min_votes", 100000),
            criteria.get("max_votes", 1000000),
        ]
        if lang != "any":
            params.append(lang)
            params.append("%" + lang + "%")
        return params

    @staticmethod
    def build_count_query(
        use_cache: bool = False,
        use_recent: bool = False,
        language: str = "any",
    ) -> str:
        """Build a COUNT(*) query parallel to build_base_query."""
        where = MovieQueryBuilder._where_clause(use_cache, use_recent, language)
        if use_recent:
            return "SELECT COUNT(*) FROM recent_movies_cache " + where
        elif use_cache:
            return "SELECT COUNT(*) FROM popular_movies_cache " + where
        else:
            return (
                "SELECT COUNT(*) "
                "FROM `title.basics` tb "
                "JOIN `title.ratings` tr ON tb.tconst = tr.tconst "
                + where
            )

    @staticmethod
    def build_genre_conditions_fulltext(criteria: dict[str, Any], use_cache: bool = False) -> tuple:
        """Build genre conditions using FULLTEXT search for better performance."""
        genres = criteria.get("genres")
        if not genres:
            return "", []

        # If 15+ genres selected, it's essentially "any genre" - skip the filter entirely
        if len(genres) >= 15:
            logger.info("%d genres selected (15+), skipping genre filter for performance", len(genres))
            return "", []

        # Use FULLTEXT search for better performance
        if use_cache:
            table_alias = ""
        else:
            table_alias = "tb."

        # Build FULLTEXT search query — strip boolean-mode operators to
        # prevent injection via crafted genre names.
        _ft_unsafe = str.maketrans("", "", '+-<>()~*"@')
        genre_search = " ".join(
            [f'+"{genre.translate(_ft_unsafe)}"' for genre in genres]
        )
        condition = f" AND MATCH({table_alias}genres) AGAINST(%s IN BOOLEAN MODE)"
        return condition, [genre_search]

    @staticmethod
    def _escape_like(value: str) -> str:
        """Escape SQL LIKE wildcards (``%`` and ``_``) in *value*."""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def build_genre_conditions(criteria: dict[str, Any], parameters: list[Any], use_cache: bool = False) -> list[str]:
        """Fallback to LIKE queries if FULLTEXT is not available."""
        genre_conditions: list[str] = []
        genres = criteria.get("genres")
        if genres:
            # If 15+ genres selected, it's essentially "any genre" - skip the filter entirely
            if len(genres) >= 15:
                logger.info("%d genres selected (15+), skipping genre filter for performance", len(genres))
                return []

            if use_cache:
                genre_conditions = [" OR ".join(["genres LIKE %s" for _ in genres])]
            else:
                genre_conditions = [" OR ".join(["tb.genres LIKE %s" for _ in genres])]
            parameters.extend(
                ["%" + MovieQueryBuilder._escape_like(genre) + "%" for genre in genres]
            )
        return genre_conditions


class ImdbRandomMovieFetcher(MovieFetcher):
    def __init__(self, database_pool, cache=None):
        self.db_pool = database_pool
        self._cache = cache
        self.use_fulltext = True

    _COUNT_CACHE_TTL = 300  # 5 minutes

    async def _get_cached_count(self, cache_key: str) -> int | None:
        """Retrieve a cached row count from Redis."""
        if not self._cache:
            return None
        try:
            cached = await self._cache.get(CacheNamespace.TEMP, cache_key)
            if cached is not None:
                logger.debug("Count cache hit for %s: %d", cache_key, cached)
                return int(cached)
        except Exception:
            logger.warning("Cache read failed for %s", cache_key, exc_info=True)
        return None

    async def _set_cached_count(self, cache_key: str, count: int) -> None:
        """Store a row count in Redis with a short TTL."""
        if not self._cache:
            return
        try:
            await self._cache.set(
                CacheNamespace.TEMP, cache_key, count, ttl=self._COUNT_CACHE_TTL
            )
        except Exception:
            logger.warning("Cache write failed for %s", cache_key, exc_info=True)

    @staticmethod
    def _table_label(use_recent: bool, use_cache: bool) -> str:
        if use_recent:
            return "recent cache"
        return "cache" if use_cache else "main tables"

    def _build_query_with_genres(self, base_query, criteria, parameters, use_cache_table):
        """Append genre conditions to *base_query* and return (query, params)."""
        if self.use_fulltext:
            genre_condition, genre_params = MovieQueryBuilder.build_genre_conditions_fulltext(
                criteria, use_cache_table
            )
            return base_query + genre_condition, parameters + genre_params

        genre_conditions = MovieQueryBuilder.build_genre_conditions(
            criteria, parameters, use_cache_table
        )
        query = base_query + (f" AND ({genre_conditions[0]})" if genre_conditions else "")
        return query, parameters

    @staticmethod
    def _resolve_table_routing(criteria: dict[str, Any]) -> tuple[bool, bool, int]:
        """Determine which table to query and return (use_cache, use_recent, current_year)."""
        current_year = datetime.now(timezone.utc).year
        use_recent = MovieQueryBuilder.should_use_recent_cache(criteria, current_year)
        use_cache = MovieQueryBuilder.should_use_cache(criteria, current_year) and not use_recent
        return use_cache, use_recent, current_year

    async def _with_fulltext_fallback(self, fn, *args, **kwargs):
        """Call *fn* and, on FULLTEXT DatabaseError, retry with LIKE queries."""
        try:
            return await fn(*args, **kwargs)
        except DatabaseError as e:
            if self.use_fulltext and "FULLTEXT" in str(e):
                logger.warning("FULLTEXT search failed, falling back to LIKE queries for this request")
                self.use_fulltext = False
                try:
                    return await fn(*args, **kwargs)
                finally:
                    self.use_fulltext = True
            raise

    async def _safe_fetch(self, fn, *args, **kwargs):
        """Run *fn* with FULLTEXT fallback, returning [] on DatabaseError."""
        try:
            return await self._with_fulltext_fallback(fn, *args, **kwargs)
        except DatabaseError as e:
            logger.error("Database error: %s", e)
            return []
        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            raise

    async def fetch_movies_by_criteria(self, criteria):
        """Fetch movies using optimized queries and indexes."""
        return await self._safe_fetch(self._fetch_movies_by_criteria_impl, criteria)

    async def _fetch_movies_by_criteria_impl(self, criteria):
        start_time = time.time()
        use_cache, use_recent, current_year = self._resolve_table_routing(criteria)
        lang = criteria.get("language", "en")

        base_query = MovieQueryBuilder.build_base_query(use_cache, use_recent, lang)
        parameters = MovieQueryBuilder.build_parameters(criteria, current_year)

        full_query, all_parameters = self._build_query_with_genres(
            base_query, criteria, parameters, use_cache or use_recent
        )

        full_query += " LIMIT %s"
        all_parameters = all_parameters + [500]

        result = await self.db_pool.execute(full_query, all_parameters, fetch="all")

        logger.info(
            "Fetched %d movies in %.2f seconds using %s",
            len(result) if result else 0,
            time.time() - start_time,
            self._table_label(use_recent, use_cache),
        )
        return result or []

    async def fetch_random_movies(self, criteria: dict[str, Any], limit: int = 15):
        """Fetch random movies using optimized cache tables."""
        return await self._safe_fetch(self._fetch_random_movies_impl, criteria, limit)

    async def _count_qualifying_rows(
        self, criteria: dict[str, Any], parameters: list[Any],
        use_cache: bool, use_recent: bool, lang: str,
    ) -> int:
        """Count rows matching criteria, using Redis cache with 5-min TTL."""
        cache_key = _criteria_cache_key(criteria)
        total_rows = await self._get_cached_count(cache_key)
        if total_rows is not None:
            return total_rows

        count_query = MovieQueryBuilder.build_count_query(
            use_cache=use_cache, use_recent=use_recent, language=lang
        )
        count_query, count_params = self._build_query_with_genres(
            count_query, criteria, parameters, use_cache or use_recent
        )
        try:
            count_result = await self.db_pool.execute(count_query, count_params, fetch="one")
        except DatabaseError:
            count_result = None
        total_rows = list(count_result.values())[0] if count_result else 0
        await self._set_cached_count(cache_key, total_rows)
        return total_rows

    async def _fetch_random_movies_impl(self, criteria: dict[str, Any], limit: int):
        method_start_time = time.time()
        use_cache, use_recent, current_year = self._resolve_table_routing(criteria)
        lang = criteria.get("language", "en")

        if use_cache:
            base_query = MovieQueryBuilder.build_base_query(use_cache=True, language=lang)
            order_by = " ORDER BY rand_order"
        else:
            base_query = MovieQueryBuilder.build_base_query(use_recent=use_recent, language=lang)
            order_by = ""

        parameters = MovieQueryBuilder.build_parameters(criteria, current_year)
        where_query, all_parameters = self._build_query_with_genres(
            base_query, criteria, parameters, use_cache or use_recent
        )

        # Random-offset strategy: count qualifying rows then pick a random
        # offset to avoid the expensive ORDER BY RAND() full-table sort.
        if not order_by:
            total_rows = await self._count_qualifying_rows(
                criteria, parameters, use_cache, use_recent, lang
            )
            if total_rows > int(limit):
                rand_offset = random.randint(0, max(0, total_rows - int(limit)))
                full_query = where_query + " LIMIT %s OFFSET %s"
                all_parameters = all_parameters + [int(limit), rand_offset]
            else:
                full_query = where_query + " LIMIT %s"
                all_parameters = all_parameters + [int(limit)]
        else:
            full_query = where_query + order_by + " LIMIT %s"
            all_parameters = all_parameters + [int(limit)]

        query_start_time = time.time()
        result = await self.db_pool.execute(full_query, all_parameters, fetch="all")

        logger.info(
            "Query executed in %.2f seconds using %s",
            time.time() - query_start_time,
            self._table_label(use_recent, use_cache),
        )
        logger.info(
            "Completed fetch_random_movies in %.2f seconds",
            time.time() - method_start_time,
        )

        return result or []
