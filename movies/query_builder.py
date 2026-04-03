import hashlib
import json
import random
import time
from datetime import datetime
from typing import Any, Dict, List

from logging_config import get_logger

from infra.errors import DatabaseError
from .interfaces import MovieFetcher

logger = get_logger(__name__)


def _criteria_cache_key(criteria: Dict[str, Any]) -> str:
    """Build a deterministic cache key from filter criteria."""
    # Sort for determinism; use a short hash to keep Redis keys compact
    blob = json.dumps(criteria, sort_keys=True, default=str).encode()
    return "count:" + hashlib.sha256(blob).hexdigest()[:16]


# Columns needed by downstream consumers (MovieNavigator only reads ``tconst``
# from the result rows, but the integration test in test_filter_backend.py
# asserts on startYear / averageRating / numVotes / titleType / language).
_CACHE_COLUMNS = "tconst, primaryTitle, startYear, genres, language, titleType, slug, averageRating, numVotes"

# Unified WHERE clause template — used by all three table paths so that
# parameter ordering is identical everywhere.
_WHERE_TEMPLATE = (
    "WHERE {p}titleType = %s "
    "AND {p}startYear BETWEEN %s AND %s "
    "AND {r}averageRating BETWEEN %s AND %s "
    "AND {r}numVotes >= %s AND {r}numVotes <= %s "
    "AND (%s = 'any' OR {p}language = %s OR {p}language LIKE %s OR {p}language IS NULL)"
)


# Helpers for query construction
class MovieQueryBuilder:
    """Build optimized SQL queries using new indexes and cache tables.

    All query variants share the same WHERE clause and parameter ordering:
    (titleType, min_year, max_year, min_rating, max_rating, min_votes,
    max_votes, language_check, language_exact, language_pattern).
    """

    @staticmethod
    def should_use_cache(criteria: Dict[str, Any], current_year: int | None = None) -> bool:
        """Determine if we should use the cache table based on criteria."""
        if current_year is None:
            current_year = datetime.now().year
        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", current_year)
        min_votes = criteria.get("min_votes", 100000)

        # Use cache for movies 1980–present with significant votes
        if min_year >= 1980 and max_year <= current_year and min_votes >= 10000:
            return True
        return False

    @staticmethod
    def should_use_recent_cache(criteria: Dict[str, Any], current_year: int | None = None) -> bool:
        """Determine if we should use the recent movies cache.

        The threshold is rolling: movies from the last 2 full calendar years
        are considered "recent" and routed to the lighter cache table to avoid
        13+ second full-table queries.
        """
        if current_year is None:
            current_year = datetime.now().year
        recent_threshold = current_year - 2  # e.g. 2024 when year is 2026

        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", current_year)

        # Only route to the recent cache when the entire requested year range
        # falls within that cache's coverage window. Broad queries like
        # 1900-current_year must not be truncated to recent releases.
        return min_year >= recent_threshold and max_year >= recent_threshold

    @staticmethod
    def _where_clause(use_cache: bool = False, use_recent: bool = False) -> str:
        """Return the shared WHERE clause with correct table alias prefixes."""
        if use_cache or use_recent:
            # Cache tables have flat columns — no alias prefix needed.
            return _WHERE_TEMPLATE.format(p="", r="")
        # Main-table path: title.basics aliased as tb, title.ratings as tr.
        return _WHERE_TEMPLATE.format(p="tb.", r="tr.")

    @staticmethod
    def build_base_query(use_cache: bool = False, use_recent: bool = False) -> str:
        """Build base SELECT query for the appropriate table."""
        where = MovieQueryBuilder._where_clause(use_cache, use_recent)
        if use_recent:
            return f"SELECT {_CACHE_COLUMNS} FROM recent_movies_cache {where}"
        elif use_cache:
            return f"SELECT {_CACHE_COLUMNS} FROM popular_movies_cache {where}"
        else:
            return (
                "SELECT tb.tconst, tb.primaryTitle, tb.startYear, tb.genres, "
                "tb.language, tb.titleType, tb.slug, tr.averageRating, tr.numVotes "
                "FROM `title.basics` tb "
                "JOIN `title.ratings` tr ON tb.tconst = tr.tconst "
                + where
            )

    @staticmethod
    def build_parameters(criteria: Dict[str, Any], current_year: int | None = None) -> List[Any]:
        """Build parameters in the unified order used by all query paths."""
        if current_year is None:
            current_year = datetime.now().year
        lang = criteria.get("language", "en")
        if lang == "any":
            language_check = "any"
            language_exact = "any"
            language_pattern = "%"
        else:
            language_check = lang
            language_exact = lang
            language_pattern = "%" + lang + "%"

        return [
            criteria.get("title_type", "movie"),
            criteria.get("min_year", 1900),
            criteria.get("max_year", current_year),
            criteria.get("min_rating", 7.0),
            criteria.get("max_rating", 10),
            criteria.get("min_votes", 100000),
            criteria.get("max_votes", 1000000),
            language_check,
            language_exact,
            language_pattern,
        ]

    @staticmethod
    def build_count_query(use_cache: bool = False, use_recent: bool = False) -> str:
        """Build a COUNT(*) query parallel to build_base_query."""
        where = MovieQueryBuilder._where_clause(use_cache, use_recent)
        if use_recent:
            return f"SELECT COUNT(*) FROM recent_movies_cache {where}"
        elif use_cache:
            return f"SELECT COUNT(*) FROM popular_movies_cache {where}"
        else:
            return (
                "SELECT COUNT(*) "
                "FROM `title.basics` tb "
                "JOIN `title.ratings` tr ON tb.tconst = tr.tconst "
                + where
            )

    @staticmethod
    def build_genre_conditions_fulltext(criteria: Dict[str, Any], use_cache: bool = False) -> tuple:
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
    def build_genre_conditions(criteria: Dict[str, Any], parameters: List[Any], use_cache: bool = False) -> List[str]:
        """Fallback to LIKE queries if FULLTEXT is not available."""
        genre_conditions: List[str] = []
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


# Set up logging

# Consumers are expected to create and manage their own DatabaseConnectionPool
# instance and pass it to ``ImdbRandomMovieFetcher``.


# Convert the ImdbRandomMovieFetcher class methods to async
class ImdbRandomMovieFetcher(MovieFetcher):
    def __init__(self, database_pool):
        self.db_pool = database_pool
        self.use_fulltext = True  # Flag to use FULLTEXT search

    _COUNT_CACHE_TTL = 300  # 5 minutes

    async def _get_cached_count(self, cache_key: str) -> int | None:
        """Retrieve a cached row count from Redis."""
        try:
            from quart import current_app
            secure_cache = getattr(current_app, "secure_cache", None)
            if secure_cache:
                from infra.cache import CacheNamespace
                cached = await secure_cache.get(CacheNamespace.TEMP, cache_key)
                if cached is not None:
                    logger.debug("Count cache hit for %s: %d", cache_key, cached)
                    return int(cached)
        except Exception:
            logger.debug("Cache read failed for %s", cache_key, exc_info=True)
        return None

    async def _set_cached_count(self, cache_key: str, count: int) -> None:
        """Store a row count in Redis with a short TTL."""
        try:
            from quart import current_app
            secure_cache = getattr(current_app, "secure_cache", None)
            if secure_cache:
                from infra.cache import CacheNamespace
                await secure_cache.set(
                    CacheNamespace.TEMP, cache_key, count, ttl=self._COUNT_CACHE_TTL
                )
        except Exception:
            logger.debug("Cache write failed for %s", cache_key, exc_info=True)

    def _build_query_with_genres(self, base_query, criteria, parameters, use_cache_table):
        """Append genre conditions to *base_query* and return (query, params)."""
        if self.use_fulltext:
            genre_condition, genre_params = MovieQueryBuilder.build_genre_conditions_fulltext(
                criteria, use_cache_table
            )
            return base_query + genre_condition, parameters + genre_params

        # Fallback to LIKE queries
        genre_conditions = MovieQueryBuilder.build_genre_conditions(
            criteria, parameters, use_cache_table
        )
        query = base_query + (f" AND ({genre_conditions[0]})" if genre_conditions else "")
        return query, parameters

    async def fetch_movies_by_criteria(self, criteria):
        """Fetch movies using optimized queries and indexes."""
        start_time = time.time()
        try:
            current_year = datetime.now().year
            use_recent = MovieQueryBuilder.should_use_recent_cache(criteria, current_year)
            use_cache = MovieQueryBuilder.should_use_cache(criteria, current_year) and not use_recent

            base_query = MovieQueryBuilder.build_base_query(use_cache, use_recent)
            parameters = MovieQueryBuilder.build_parameters(criteria, current_year)

            full_query, all_parameters = self._build_query_with_genres(
                base_query, criteria, parameters, use_cache or use_recent
            )

            # Safety LIMIT to prevent unbounded result sets on broad filters
            full_query += " LIMIT %s"
            all_parameters = all_parameters + [500]

            logger.debug("Using %s table", 'recent cache' if use_recent else 'cache' if use_cache else 'main')

            result = await self.db_pool.execute(full_query, all_parameters, "all")

            logger.info(
                "Fetched %d movies in %.2f seconds using %s",
                len(result) if result else 0,
                time.time() - start_time,
                'recent cache' if use_recent else 'cache' if use_cache else 'main tables'
            )
            return result or []
        except DatabaseError as e:
            if self.use_fulltext and "FULLTEXT" in str(e):
                logger.warning("FULLTEXT search failed, falling back to LIKE queries for this request")
                # Use a local flag so the fallback is per-request, not permanent.
                saved = self.use_fulltext
                self.use_fulltext = False
                try:
                    return await self.fetch_movies_by_criteria(criteria)
                finally:
                    self.use_fulltext = saved
            logger.error("Database error fetching movies by criteria: %s", e)
            return []
        except Exception as e:
            logger.error("Error fetching movies by criteria: %s", e, exc_info=True)
            raise

    async def fetch_random_movies(self, criteria: Dict[str, Any], limit: int = 15):
        """Fetch random movies using optimized cache tables."""
        method_start_time = time.time()
        logger.info("Starting fetch_random_movies with criteria: %s and limit: %s", criteria, limit)

        try:
            current_year = datetime.now().year
            use_recent = MovieQueryBuilder.should_use_recent_cache(criteria, current_year)
            use_cache = MovieQueryBuilder.should_use_cache(criteria, current_year) and not use_recent

            if use_cache:
                base_query = MovieQueryBuilder.build_base_query(use_cache=True)
                order_by = " ORDER BY rand_order"
            elif use_recent:
                base_query = MovieQueryBuilder.build_base_query(use_recent=True)
                order_by = ""  # handled via random-offset strategy below
            else:
                # Avoid ORDER BY RAND() on the full table — use a random
                # offset into the qualifying rows instead.  This turns an
                # O(n·log n) filesort into two index scans.
                base_query = MovieQueryBuilder.build_base_query()
                order_by = ""

            parameters = MovieQueryBuilder.build_parameters(criteria, current_year)

            where_query, all_parameters = self._build_query_with_genres(
                base_query, criteria, parameters, use_cache or use_recent
            )

            # Random-offset strategy: for the full table or recent cache,
            # count qualifying rows then pick a random offset to avoid the
            # expensive ORDER BY RAND() full-table sort.
            #
            # The count is cached in Redis (5 min TTL) to skip the expensive
            # COUNT query on repeated requests with the same filters.
            if not order_by:
                cache_key = _criteria_cache_key(criteria)
                total_rows = await self._get_cached_count(cache_key)

                if total_rows is None:
                    count_query = MovieQueryBuilder.build_count_query(
                        use_cache=use_cache, use_recent=use_recent
                    )
                    # Reuse the same genre condition for the count query
                    count_query, count_params = self._build_query_with_genres(
                        count_query, criteria, parameters, use_cache or use_recent
                    )
                    try:
                        count_result = await self.db_pool.execute(
                            count_query, count_params, "one"
                        )
                    except DatabaseError:
                        count_result = None
                    total_rows = list(count_result.values())[0] if count_result else 0
                    await self._set_cached_count(cache_key, total_rows)
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
            result = await self.db_pool.execute(full_query, all_parameters, "all")
            query_end_time = time.time()

            logger.info(
                "Query executed in %.2f seconds using %s",
                query_end_time - query_start_time,
                'recent cache' if use_recent else 'cache' if use_cache else 'main tables'
            )

            logger.info(
                "Completed fetch_random_movies in %.2f seconds",
                time.time() - method_start_time,
            )

            return result or []

        except DatabaseError as e:
            if self.use_fulltext and "FULLTEXT" in str(e):
                logger.warning("FULLTEXT search failed, falling back to LIKE queries for this request")
                saved = self.use_fulltext
                self.use_fulltext = False
                try:
                    return await self.fetch_random_movies(criteria, limit)
                finally:
                    self.use_fulltext = saved
            logger.error("Database error in fetch_random_movies: %s", e)
            return []
        except Exception as e:
            logger.error("Error in fetch_random_movies: %s", e, exc_info=True)
            raise
