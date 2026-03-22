from logging_config import get_logger
import time
import traceback
from typing import Any, Dict, List

from database.errors import DatabaseError
from .interfaces import MovieFetcher

logger = get_logger(__name__)


# Helpers for query construction
class MovieQueryBuilder:
    """Build optimized SQL queries using new indexes and cache tables."""

    @staticmethod
    def should_use_cache(criteria: Dict[str, Any]) -> bool:
        """Determine if we should use the cache table based on criteria."""
        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", 2025)
        min_votes = criteria.get("min_votes", 100000)
        
        # Use cache for movies 1980–present with significant votes
        from datetime import datetime
        if min_year >= 1980 and max_year <= datetime.now().year and min_votes >= 10000:
            return True
        return False

    @staticmethod
    def should_use_recent_cache(criteria: Dict[str, Any]) -> bool:
        """Determine if we should use the recent movies cache."""
        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", 2025)
        
        # Use recent cache for any 2024+ movies (regardless of vote count)
        # This is critical for performance since 2024+ movies cause 13+ second queries
        return min_year >= 2024 or (min_year < 2024 and max_year >= 2024)

    @staticmethod
    def build_base_query(use_cache: bool = False, use_recent: bool = False) -> str:
        """Build base query optimized for new indexes."""
        if use_recent:
            return (
                "SELECT * FROM recent_movies_cache "
                "WHERE startYear BETWEEN %s AND %s "
                "AND averageRating BETWEEN %s AND %s "
                "AND numVotes >= %s AND numVotes <= %s "
                "AND titleType = %s "
                "AND (%s = 'any' OR language LIKE %s OR language IS NULL)"
            )
        elif use_cache:
            return (
                "SELECT * FROM popular_movies_cache "
                "WHERE startYear BETWEEN %s AND %s "
                "AND averageRating BETWEEN %s AND %s "
                "AND numVotes >= %s AND numVotes <= %s "
                "AND titleType = %s "
                "AND (%s = 'any' OR language LIKE %s OR language IS NULL)"
            )
        else:
            # Use force index hint for better performance
            return (
                "SELECT tb.* "
                "FROM `title.basics` tb FORCE INDEX (idx_basics_compound) "
                "JOIN `title.ratings` tr FORCE INDEX (idx_ratings_compound) ON tb.tconst = tr.tconst "
                "WHERE tb.titleType = %s "
                "AND tb.startYear BETWEEN %s AND %s "
                "AND tr.numVotes >= %s AND tr.numVotes <= %s "
                "AND tr.averageRating BETWEEN %s AND %s "
                "AND (%s = 'any' OR tb.language LIKE %s OR tb.language IS NULL)"
            )

    @staticmethod
    def build_parameters(criteria: Dict[str, Any], optimized: bool = False) -> List[Any]:
        """Build parameters in the order expected by optimized queries."""
        lang = criteria.get("language", "en")
        if lang == "any":
            language_check = "any"
            language_pattern = "%"
        else:
            language_check = lang
            language_pattern = "%" + lang + "%"
        
        if optimized:
            # Reordered for optimized query (titleType first)
            return [
                criteria.get("title_type", "movie"),
                criteria.get("min_year", 1900),
                criteria.get("max_year", 2025),
                criteria.get("min_votes", 100000),
                criteria.get("max_votes", 1000000),
                criteria.get("min_rating", 7.0),
                criteria.get("max_rating", 10),
                language_check,
                language_pattern,
            ]
        else:
            # Original order for cache tables
            return [
                criteria.get("min_year", 1900),
                criteria.get("max_year", 2025),
                criteria.get("min_rating", 7.0),
                criteria.get("max_rating", 10),
                criteria.get("min_votes", 100000),
                criteria.get("max_votes", 1000000),
                criteria.get("title_type", "movie"),
                language_check,
                language_pattern,
            ]

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
            
        # Build FULLTEXT search query
        genre_search = " ".join([f'+"{genre}"' for genre in genres])
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

    async def fetch_movies_by_criteria(self, criteria):
        """Fetch movies using optimized queries and indexes."""
        start_time = time.time()
        try:
            # Determine which table to use
            use_recent = MovieQueryBuilder.should_use_recent_cache(criteria)
            use_cache = MovieQueryBuilder.should_use_cache(criteria) and not use_recent
            use_optimized = not use_cache and not use_recent
            
            base_query = MovieQueryBuilder.build_base_query(use_cache, use_recent)
            parameters = MovieQueryBuilder.build_parameters(criteria, use_optimized)
            
            # Try FULLTEXT search first
            if self.use_fulltext:
                genre_condition, genre_params = MovieQueryBuilder.build_genre_conditions_fulltext(
                    criteria, use_cache or use_recent
                )
                full_query = base_query + genre_condition
                all_parameters = parameters + genre_params
            else:
                # Fallback to LIKE queries
                genre_conditions = MovieQueryBuilder.build_genre_conditions(
                    criteria, parameters, use_cache or use_recent
                )
                full_query = base_query + (f" AND ({genre_conditions[0]})" if genre_conditions else "")
                all_parameters = parameters

            logger.debug("Using %s table", 'recent cache' if use_recent else 'cache' if use_cache else 'main')
            logger.debug("Executing optimized query with parameters: %s", all_parameters)

            result = await self.db_pool.execute(full_query, all_parameters, "all")

            logger.info(
                "Fetched %d movies in %.2f seconds using %s",
                len(result) if result else 0,
                time.time() - start_time,
                'recent cache' if use_recent else 'cache' if use_cache else 'optimized indexes'
            )
            return result or []
        except DatabaseError as e:
            # If FULLTEXT fails, retry with LIKE
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
        logger.info("Starting optimized fetch_random_movies with criteria: %s and limit: %s", criteria, limit)

        try:
            # Determine which table to use
            use_recent = MovieQueryBuilder.should_use_recent_cache(criteria)
            use_cache = MovieQueryBuilder.should_use_cache(criteria) and not use_recent
            use_optimized = not use_cache and not use_recent
            
            if use_cache:
                # Use pre-randomized cache table for better performance
                base_query = MovieQueryBuilder.build_base_query(use_cache=True)
                order_by = " ORDER BY rand_order"
            elif use_recent:
                # Use random-offset strategy instead of ORDER BY RAND()
                base_query = MovieQueryBuilder.build_base_query(use_recent=True)
                order_by = ""  # handled via random-offset strategy below
            else:
                # Avoid ORDER BY RAND() on the full table — use a random
                # offset into the qualifying rows instead.  This turns an
                # O(n·log n) filesort into two index scans.
                base_query = MovieQueryBuilder.build_base_query()
                order_by = ""  # handled below via random-offset strategy
            
            parameters = MovieQueryBuilder.build_parameters(criteria, use_optimized)
            
            # Build the WHERE portion (without ORDER BY / LIMIT yet)
            if self.use_fulltext:
                genre_condition, genre_params = MovieQueryBuilder.build_genre_conditions_fulltext(
                    criteria, use_cache or use_recent
                )
                where_query = base_query + genre_condition
                all_parameters = parameters + genre_params
            else:
                genre_conditions = MovieQueryBuilder.build_genre_conditions(
                    criteria, parameters, use_cache or use_recent
                )
                where_query = base_query + (f" AND ({genre_conditions[0]})" if genre_conditions else "")
                all_parameters = parameters

            # Random-offset strategy: for the full table or recent cache,
            # count qualifying rows then pick a random offset to avoid the
            # expensive ORDER BY RAND() full-table sort.
            if not order_by:
                import random
                import re
                count_query = re.sub(r'SELECT\s+\S+', 'SELECT COUNT(*)', where_query, count=1)
                try:
                    count_result = await self.db_pool.execute(
                        count_query, all_parameters, "one"
                    )
                except DatabaseError:
                    count_result = None
                total_rows = list(count_result.values())[0] if count_result else 0
                if total_rows > int(limit):
                    rand_offset = random.randint(0, max(0, total_rows - int(limit)))
                    full_query = where_query + f" LIMIT {int(limit)} OFFSET {rand_offset}"
                else:
                    full_query = where_query + f" LIMIT {int(limit)}"
            else:
                full_query = where_query + order_by + f" LIMIT {int(limit)}"

            query_start_time = time.time()
            result = await self.db_pool.execute(full_query, all_parameters, "all")
            query_end_time = time.time()

            logger.info(
                "Optimized query executed in %.2f seconds using %s",
                query_end_time - query_start_time,
                'recent cache' if use_recent else 'cache with pre-random' if use_cache else 'optimized indexes'
            )

            method_end_time = time.time()
            logger.info(
                "Completed optimized fetch_random_movies in %.2f seconds (%.1fx faster)",
                method_end_time - method_start_time,
                2.4 / (method_end_time - method_start_time) if (method_end_time - method_start_time) > 0 else 1
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
            logger.error("Error in optimized fetch: %s\n%s", e, traceback.format_exc())
            raise


def extract_movie_filter_criteria(form_data):
    """
    Extract filter criteria from the form data.



    Returns:
        dict: Dictionary containing the filter criteria.
    """

    # Initialize an empty criteria dictionary
    criteria = {}

    # Handling various other criteria (year, IMDb score, number of votes)
    if form_data.get('year_min'):
        criteria['min_year'] = int(form_data.get('year_min'))
    if form_data.get('year_max'):
        criteria['max_year'] = int(form_data.get('year_max'))
    if form_data.get('imdb_score_min'):
        criteria['min_rating'] = float(form_data.get('imdb_score_min'))
    if form_data.get('imdb_score_max'):
        criteria['max_rating'] = float(form_data.get('imdb_score_max'))
    if form_data.get('num_votes_min'):
        criteria['min_votes'] = int(form_data.get('num_votes_min'))
    if form_data.get('num_votes_max'):
        criteria['max_votes'] = int(form_data.get('num_votes_max'))

    # Handling genre criteria
    genres = form_data.getlist('genres[]')
    if genres:
        criteria['genres'] = genres

    # Handling language criteria - support user selection
    language = form_data.get('language', 'en')
    criteria['language'] = language
    logger.debug("Language filter set to: %s", language)

    return criteria
