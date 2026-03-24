from datetime import datetime
from logging_config import get_logger
import time
import traceback
from typing import Any, Dict, List

from database.errors import DatabaseError
from .interfaces import MovieFetcher

logger = get_logger(__name__)


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
    "AND (%s = 'any' OR {p}language LIKE %s OR {p}language IS NULL)"
)


# Helpers for query construction
class MovieQueryBuilder:
    """Build optimized SQL queries using new indexes and cache tables.

    All query variants share the same WHERE clause and parameter ordering:
    (titleType, min_year, max_year, min_rating, max_rating, min_votes,
    max_votes, language_check, language_pattern).
    """

    @staticmethod
    def should_use_cache(criteria: Dict[str, Any]) -> bool:
        """Determine if we should use the cache table based on criteria."""
        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", datetime.now().year)
        min_votes = criteria.get("min_votes", 100000)

        # Use cache for movies 1980–present with significant votes
        if min_year >= 1980 and max_year <= datetime.now().year and min_votes >= 10000:
            return True
        return False

    @staticmethod
    def should_use_recent_cache(criteria: Dict[str, Any]) -> bool:
        """Determine if we should use the recent movies cache.

        The threshold is rolling: movies from the last 2 full calendar years
        are considered "recent" and routed to the lighter cache table to avoid
        13+ second full-table queries.
        """
        recent_threshold = datetime.now().year - 2  # e.g. 2024 when year is 2026

        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", datetime.now().year)

        return min_year >= recent_threshold or max_year >= recent_threshold

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
    def build_parameters(criteria: Dict[str, Any]) -> List[Any]:
        """Build parameters in the unified order used by all query paths."""
        lang = criteria.get("language", "en")
        if lang == "any":
            language_check = "any"
            language_pattern = "%"
        else:
            language_check = lang
            language_pattern = "%" + lang + "%"

        return [
            criteria.get("title_type", "movie"),
            criteria.get("min_year", 1900),
            criteria.get("max_year", datetime.now().year),
            criteria.get("min_rating", 7.0),
            criteria.get("max_rating", 10),
            criteria.get("min_votes", 100000),
            criteria.get("max_votes", 1000000),
            language_check,
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
            parameters.extend(["%" + genre + "%" for genre in genres])
        return genre_conditions


# Set up logging

# Consumers are expected to create and manage their own DatabaseConnectionPool
# instance and pass it to ``ImdbRandomMovieFetcher``.


# Convert the ImdbRandomMovieFetcher class methods to async
class ImdbRandomMovieFetcher(MovieFetcher):
    def __init__(self, database_pool):
        self.db_pool = database_pool
        self.use_fulltext = True  # Flag to use FULLTEXT search

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
            use_recent = MovieQueryBuilder.should_use_recent_cache(criteria)
            use_cache = MovieQueryBuilder.should_use_cache(criteria) and not use_recent

            base_query = MovieQueryBuilder.build_base_query(use_cache, use_recent)
            parameters = MovieQueryBuilder.build_parameters(criteria)

            full_query, all_parameters = self._build_query_with_genres(
                base_query, criteria, parameters, use_cache or use_recent
            )

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
                logger.warning("FULLTEXT search failed, falling back to LIKE queries")
                self.use_fulltext = False
                return await self.fetch_movies_by_criteria(criteria)
            logger.error("Database error fetching movies by criteria: %s", e)
            return []
        except Exception as e:
            logger.error("Error fetching movies by criteria: %s\n%s", e, traceback.format_exc())
            raise

    async def fetch_random_movies(self, criteria: Dict[str, Any], limit: int = 15):
        """Fetch random movies using optimized cache tables."""
        method_start_time = time.time()
        logger.info("Starting fetch_random_movies with criteria: %s and limit: %s", criteria, limit)

        try:
            use_recent = MovieQueryBuilder.should_use_recent_cache(criteria)
            use_cache = MovieQueryBuilder.should_use_cache(criteria) and not use_recent

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

            parameters = MovieQueryBuilder.build_parameters(criteria)

            where_query, all_parameters = self._build_query_with_genres(
                base_query, criteria, parameters, use_cache or use_recent
            )

            # Random-offset strategy: for the full table or recent cache,
            # count qualifying rows then pick a random offset to avoid the
            # expensive ORDER BY RAND() full-table sort.
            if not order_by:
                import random
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
                logger.warning("FULLTEXT search failed, falling back to LIKE queries")
                self.use_fulltext = False
                return await self.fetch_random_movies(criteria, limit)
            logger.error("Database error in fetch_random_movies: %s", e)
            return []
        except Exception as e:
            logger.error("Error in fetch_random_movies: %s\n%s", e, traceback.format_exc())
            raise


def _safe_int(value: str | None, default: int | None = None, *, min_val: int | None = None, max_val: int | None = None) -> int | None:
    """Parse an integer from form input, returning *default* on failure."""
    if not value:
        return default
    try:
        n = int(value)
    except (ValueError, TypeError):
        return default
    if min_val is not None:
        n = max(n, min_val)
    if max_val is not None:
        n = min(n, max_val)
    return n


def _safe_float(value: str | None, default: float | None = None, *, min_val: float | None = None, max_val: float | None = None) -> float | None:
    """Parse a float from form input, returning *default* on failure."""
    if not value:
        return default
    try:
        f = float(value)
    except (ValueError, TypeError):
        return default
    if min_val is not None:
        f = max(f, min_val)
    if max_val is not None:
        f = min(f, max_val)
    return f


def extract_movie_filter_criteria(form_data):
    """Extract and validate filter criteria from form data.

    Returns:
        dict: Dictionary containing the validated filter criteria.
    """
    criteria = {}

    # Year range — clamp to sensible bounds
    min_year = _safe_int(form_data.get('year_min'), min_val=1888, max_val=2100)
    max_year = _safe_int(form_data.get('year_max'), min_val=1888, max_val=2100)
    if min_year is not None:
        criteria['min_year'] = min_year
    if max_year is not None:
        criteria['max_year'] = max_year

    # IMDb score — 0.0 to 10.0
    min_rating = _safe_float(form_data.get('imdb_score_min'), min_val=0.0, max_val=10.0)
    max_rating = _safe_float(form_data.get('imdb_score_max'), min_val=0.0, max_val=10.0)
    if min_rating is not None:
        criteria['min_rating'] = min_rating
    if max_rating is not None:
        criteria['max_rating'] = max_rating

    # Vote counts — non-negative
    min_votes = _safe_int(form_data.get('num_votes_min'), min_val=0)
    max_votes = _safe_int(form_data.get('num_votes_max'), min_val=0)
    if min_votes is not None:
        criteria['min_votes'] = min_votes
    if max_votes is not None:
        criteria['max_votes'] = max_votes

    # Genres — filter to non-empty strings only
    genres = [g for g in form_data.getlist('genres[]') if g and isinstance(g, str)]
    if genres:
        criteria['genres'] = genres

    # Language — accept short ISO codes only (e.g. "en", "fr", "any")
    language = form_data.get('language', 'en')
    if not isinstance(language, str) or len(language) > 10:
        language = 'en'
    criteria['language'] = language
    logger.debug("Language filter set to: %s", language)

    return criteria
