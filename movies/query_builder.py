from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Any

from logging_config import get_logger

from infra.errors import DatabaseError
from infra.time_utils import current_year as _current_year
from movies.movie_count_cache import (
    MovieCountCache,
    bump_count_cache_generation,
    criteria_cache_key as _criteria_cache_key,
    current_count_generation as _current_count_generation,
)
from .interfaces import MovieFetcher

logger = get_logger(__name__)


_FETCH_MOVIES_LIMIT = 500


def is_fulltext_index_error(exc: Exception) -> bool:
    """Return True when *exc* looks like a MySQL FULLTEXT index failure.

    Shared between ``ImdbRandomMovieFetcher._with_fulltext_fallback`` and
    ``CandidateStore.fetch_candidate_refs_for_criteria`` so both paths use
    a single detector rather than independent string matchers.
    """
    return "fulltext" in str(exc).lower()


# Columns needed by downstream consumers (MovieNavigator only reads ``tconst``
# from the result rows, but the integration test in test_filter_backend.py
# asserts on startYear / averageRating / numVotes / titleType / language).
_CACHE_COLUMNS = (
    "tconst, primaryTitle, startYear, genres, language, titleType, slug, averageRating, numVotes"
)

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
            current_year = _current_year()
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
            current_year = _current_year()
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
                "JOIN `title.ratings` tr ON tb.tconst = tr.tconst " + where
            )

    @staticmethod
    def build_parameters(criteria: dict[str, Any], current_year: int | None = None) -> list[Any]:
        """Build parameters matching the WHERE clause from ``_where_clause``.

        Returns 7 base params when ``language='any'`` (no language predicate),
        or 9 params when a specific language is requested.
        """
        if current_year is None:
            current_year = _current_year()
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
                "JOIN `title.ratings` tr ON tb.tconst = tr.tconst " + where
            )

    @staticmethod
    def build_genre_conditions_fulltext(criteria: dict[str, Any], use_cache: bool = False) -> tuple:
        """Build genre conditions using FULLTEXT search for better performance."""
        genres = criteria.get("genres")
        if not genres:
            return "", []

        # If 15+ genres selected, it's essentially "any genre" - skip the filter entirely
        if len(genres) >= 15:
            logger.info(
                "%d genres selected (15+), skipping genre filter for performance", len(genres)
            )
            return "", []

        # Use FULLTEXT search for better performance
        if use_cache:
            table_alias = ""
        else:
            table_alias = "tb."

        # Build FULLTEXT search query — strip boolean-mode operators to
        # prevent injection via crafted genre names.
        _ft_unsafe = str.maketrans("", "", '+-<>()~*"@')
        genre_search = " ".join([f'+"{genre.translate(_ft_unsafe)}"' for genre in genres])
        condition = f" AND MATCH({table_alias}genres) AGAINST(%s IN BOOLEAN MODE)"
        return condition, [genre_search]

    @staticmethod
    def _escape_like(value: str) -> str:
        """Escape SQL LIKE wildcards (``%`` and ``_``) in *value*."""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def build_genre_conditions(
        criteria: dict[str, Any], parameters: list[Any], use_cache: bool = False
    ) -> list[str]:
        """Fallback to LIKE queries if FULLTEXT is not available."""
        genre_conditions: list[str] = []
        genres = criteria.get("genres")
        if genres:
            # If 15+ genres selected, it's essentially "any genre" - skip the filter entirely
            if len(genres) >= 15:
                logger.info(
                    "%d genres selected (15+), skipping genre filter for performance", len(genres)
                )
                return []

            if use_cache:
                genre_conditions = [" AND ".join(["genres LIKE %s" for _ in genres])]
            else:
                genre_conditions = [" AND ".join(["tb.genres LIKE %s" for _ in genres])]
            parameters.extend(
                ["%" + MovieQueryBuilder._escape_like(genre) + "%" for genre in genres]
            )
        return genre_conditions

    @staticmethod
    def genre_clause(
        criteria: dict[str, Any],
        *,
        use_fulltext: bool = True,
        use_cache: bool = False,
    ) -> tuple[str, list[Any]]:
        """Unified genre-clause builder.

        Dispatches to the FULLTEXT or LIKE variant depending on
        *use_fulltext*. Returns ``(clause, params)`` where *clause* is an
        empty string when no genre filter applies. The LIKE path is wrapped
        in an extra ``AND (...)`` to match the pattern previously used by
        ``CandidateStore._genre_clause`` and ``_build_query_with_genres``.
        """
        if use_fulltext:
            return MovieQueryBuilder.build_genre_conditions_fulltext(criteria, use_cache=use_cache)
        genre_params: list[Any] = []
        genre_conditions = MovieQueryBuilder.build_genre_conditions(
            criteria, genre_params, use_cache=use_cache
        )
        if not genre_conditions:
            return "", []
        return f" AND ({genre_conditions[0]})", genre_params


class ImdbRandomMovieFetcher(MovieFetcher):
    def __init__(self, database_pool, cache=None):
        self.db_pool = database_pool
        self._cache = cache
        self.use_fulltext = True
        self._count_cache = MovieCountCache(cache)

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
        current_year = _current_year()
        use_recent = MovieQueryBuilder.should_use_recent_cache(criteria, current_year)
        use_cache = MovieQueryBuilder.should_use_cache(criteria, current_year) and not use_recent
        return use_cache, use_recent, current_year

    async def _with_fulltext_fallback(self, fn, *args, **kwargs):
        """Call *fn* and, on FULLTEXT DatabaseError, retry with LIKE queries."""
        try:
            return await fn(*args, **kwargs)
        except DatabaseError as e:
            if self.use_fulltext and is_fulltext_index_error(e):
                logger.warning(
                    "FULLTEXT search failed, falling back to LIKE queries for this request"
                )
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
        all_parameters = all_parameters + [_FETCH_MOVIES_LIMIT]

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
        self,
        criteria: dict[str, Any],
        parameters: list[Any],
        use_cache: bool,
        use_recent: bool,
        lang: str,
    ) -> int:
        """Compatibility wrapper around MovieCountCache."""
        return await self._count_cache.count_qualifying_rows(
            criteria=criteria,
            parameters=parameters,
            use_cache=use_cache,
            use_recent=use_recent,
            lang=lang,
            db_pool=self.db_pool,
            query_builder=MovieQueryBuilder,
            build_query_with_genres=self._build_query_with_genres,
        )

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
