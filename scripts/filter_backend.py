import asyncio
import logging
from logging_config import get_logger
import os
import time
import traceback
from typing import Any, Dict, List


from settings import Config, DatabaseConnectionPool
from db_utils import DatabaseQueryExecutor
from .interfaces import MovieFetcher

# Use os.path.dirname to go up one level from the current script's directory
# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

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
        
        # Use cache for movies 1980-2023 with significant votes
        if min_year >= 1980 and max_year <= 2023 and min_votes >= 10000:
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
            logger.info(f"{len(genres)} genres selected (15+), skipping genre filter for performance")
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
                logger.info(f"{len(genres)} genres selected (15+), skipping genre filter for performance")
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
        self.db_query_executor = DatabaseQueryExecutor(database_pool)
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

            logger.debug(f"Using {'recent cache' if use_recent else 'cache' if use_cache else 'main'} table")
            logger.debug("Executing optimized query with parameters: %s", all_parameters)

            result = await self.db_query_executor.execute_async_query(full_query, all_parameters, 'all')

            logger.info(
                "Fetched %d movies in %.2f seconds using %s",
                len(result),
                time.time() - start_time,
                'recent cache' if use_recent else 'cache' if use_cache else 'optimized indexes'
            )
            return result
        except Exception as e:
            logger.error(f"Error fetching movies by criteria: {e}\n{traceback.format_exc()}")
            # If FULLTEXT fails, retry with LIKE
            if self.use_fulltext and "FULLTEXT" in str(e):
                logger.warning("FULLTEXT search failed, falling back to LIKE queries")
                self.use_fulltext = False
                return await self.fetch_movies_by_criteria(criteria)
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
                base_query = MovieQueryBuilder.build_base_query(use_recent=True)
                order_by = " ORDER BY RAND()"
            else:
                base_query = MovieQueryBuilder.build_base_query()
                order_by = " ORDER BY RAND()"
            
            parameters = MovieQueryBuilder.build_parameters(criteria, use_optimized)
            
            # Use FULLTEXT for genres if available
            if self.use_fulltext:
                genre_condition, genre_params = MovieQueryBuilder.build_genre_conditions_fulltext(
                    criteria, use_cache or use_recent
                )
                full_query = base_query + genre_condition + order_by + f" LIMIT {int(limit)}"
                all_parameters = parameters + genre_params
            else:
                genre_conditions = MovieQueryBuilder.build_genre_conditions(
                    criteria, parameters, use_cache or use_recent
                )
                full_query = base_query + (f" AND ({genre_conditions[0]})" if genre_conditions else "") + \
                            order_by + f" LIMIT {int(limit)}"
                all_parameters = parameters

            query_start_time = time.time()
            result = await self.db_query_executor.execute_async_query(full_query, all_parameters, 'all')
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

            return result
            
        except Exception as e:
            logger.error(f"Error in optimized fetch: {e}\n{traceback.format_exc()}")
            # Fallback to LIKE if FULLTEXT fails
            if self.use_fulltext and "FULLTEXT" in str(e):
                logger.warning("FULLTEXT search failed, falling back to LIKE queries")
                self.use_fulltext = False
                return await self.fetch_random_movies(criteria, limit)
            raise

    # async def fetch_random_movie(self, criteria):
    #     base_query = build_base_query()
    #     parameters = build_parameters(criteria)
    #     genre_conditions = build_genre_conditions(criteria, parameters)
    #     full_query = base_query + (
    #         f" AND ({genre_conditions[0]})" if genre_conditions else "") + " ORDER BY RAND() LIMIT 1"
    #     return await self.db_query_executor.execute_async_query(full_query, parameters)


def extract_movie_filter_criteria(form_data):
    """
    Extract filter criteria from the form data.



    Returns:
        dict: Dictionary containing the filter criteria.
    """

    # Initialize an empty criteria dictionary
    criteria = {}

    # Range criteria with explicit "no min/max" support
    # Year
    if 'year_no_min' in form_data:
        criteria['min_year'] = 1900
    elif form_data.get('year_min'):
        criteria['min_year'] = int(form_data.get('year_min'))
    if 'year_no_max' in form_data:
        criteria['max_year'] = 2025
    elif form_data.get('year_max'):
        criteria['max_year'] = int(form_data.get('year_max'))

    # IMDb Score
    if 'score_no_min' in form_data:
        criteria['min_rating'] = 1.0
    elif form_data.get('imdb_score_min'):
        criteria['min_rating'] = float(form_data.get('imdb_score_min'))
    if 'score_no_max' in form_data:
        criteria['max_rating'] = 10.0
    elif form_data.get('imdb_score_max'):
        criteria['max_rating'] = float(form_data.get('imdb_score_max'))

    # Votes
    if 'votes_no_min' in form_data:
        criteria['min_votes'] = 0
    elif form_data.get('num_votes_min'):
        criteria['min_votes'] = int(form_data.get('num_votes_min'))
    if 'votes_no_max' in form_data:
        criteria['max_votes'] = 2000000
    elif form_data.get('num_votes_max'):
        criteria['max_votes'] = int(form_data.get('num_votes_max'))

    # Handling genre criteria
    genres = form_data.getlist('genres[]')
    if genres:
        criteria['genres'] = genres

    # Handling language criteria - support user selection
    language = form_data.get('language', 'en')
    criteria['language'] = language
    logger.debug(f"Language filter set to: {language}")

    return criteria


async def main():
    """Example usage for manual testing."""
    db_config = Config.get_db_config()
    pool = DatabaseConnectionPool(db_config)
    await pool.init_pool()

    criteria = {
        'min_year': 2000,
        'max_year': 2020,
        'min_rating': 7.0,
        'max_rating': 10,
        'min_votes': 10000,
        'max_votes': 100000,
        'title_type': 'movie',
        'language': 'en',
        'genres': ['Action', 'Drama']
    }

    fetcher = ImdbRandomMovieFetcher(pool)
    movies = await fetcher.fetch_movies_by_criteria(criteria)

    for counter, movie in enumerate(movies, start=1):
        logger.debug("Movie %s: %s", counter, movie)

    await pool.close_pool()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
