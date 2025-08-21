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
    """Build SQL queries for fetching movies based on criteria."""

    @staticmethod
    def build_base_query() -> str:
        return (
            "SELECT tb.* "
            "FROM `title.basics` tb "
            "JOIN `title.ratings` tr ON tb.tconst = tr.tconst "
            "WHERE tb.startYear BETWEEN %s AND %s "
            "AND tr.averagerating BETWEEN %s AND %s "
            "AND tr.numVotes >= %s AND tr.numVotes <= %s "
            "AND tb.titleType = %s "
            "AND (%s = 'any' OR tb.language LIKE %s OR tb.language IS NULL)"  # Handle 'any' language option
        )

    @staticmethod
    def build_parameters(criteria: Dict[str, Any]) -> List[Any]:
        # Handle language parameter - 'any' means no language filter
        lang = criteria.get("language", "en")
        if lang == "any":
            language_check = "any"
            language_pattern = "%"  # Won't be used but needed for parameter count
        else:
            language_check = lang
            language_pattern = "%" + lang + "%"
        
        return [
            criteria.get("min_year", 1900),
            criteria.get("max_year", 2025),  # Updated default to include recent movies
            criteria.get("min_rating", 7.0),
            criteria.get("max_rating", 10),
            criteria.get("min_votes", 100000),
            criteria.get("max_votes", 1000000),
            criteria.get("title_type", "movie"),
            language_check,
            language_pattern,
        ]

    @staticmethod
    def build_genre_conditions(criteria: Dict[str, Any], parameters: List[Any]) -> List[str]:
        genre_conditions: List[str] = []
        genres = criteria.get("genres")
        if genres:
            # If 15+ genres are selected, it's essentially "any genre" - skip the filter
            if len(genres) >= 15:
                logger.info("15+ genres selected, skipping genre filter for performance")
                return []
            genre_conditions = [" OR ".join(["tb.genres LIKE %s" for _ in genres])]
            parameters.extend(["%" + genre + "%" for genre in genres])
        return genre_conditions


# Set up logging

# Consumers are expected to create and manage their own DatabaseConnectionPool
# instance and pass it to ``ImdbRandomMovieFetcher``.


# Convert the ImdbRandomMovieFetcher class methods to async
class ImdbRandomMovieFetcher(MovieFetcher):
    def __init__(self, database_pool):
        # Use the provided database pool
        self.db_query_executor = DatabaseQueryExecutor(database_pool)

    async def fetch_movies_by_criteria(self, criteria):
        start_time = time.time()
        try:
            base_query = MovieQueryBuilder.build_base_query()
            parameters = MovieQueryBuilder.build_parameters(criteria)
            genre_conditions = MovieQueryBuilder.build_genre_conditions(criteria, parameters)
            full_query = base_query + (f" AND ({genre_conditions[0]})" if genre_conditions else "")

            logger.debug("Executing query with parameters: %s", parameters)

            result = await self.db_query_executor.execute_async_query(full_query, parameters, 'all')

            logger.debug(
                "Fetched %d movies by criteria in %.2f seconds",
                len(result),
                time.time() - start_time,
            )
            return result
        except Exception as e:
            logger.error(f"Error fetching movies by criteria: {e}\n{traceback.format_exc()}")
            raise

    async def fetch_random_movies(self, criteria: Dict[str, Any], limit: int = 15):
        """Fetch ``limit`` random movies that match the given criteria."""

        method_start_time = time.time()

        logger.info("Starting fetch_random_movies with criteria: %s and limit: %s", criteria, limit)

        base_query = MovieQueryBuilder.build_base_query()
        parameters = MovieQueryBuilder.build_parameters(criteria)
        genre_conditions = MovieQueryBuilder.build_genre_conditions(criteria, parameters)

        if genre_conditions:
            logger.debug("Genre conditions applied: %s", genre_conditions[0])

        full_query = base_query + (
            f" AND ({genre_conditions[0]})" if genre_conditions else "") + f" ORDER BY RAND() LIMIT {int(limit)}"

        query_start_time = time.time()
        result = await self.db_query_executor.execute_async_query(full_query, parameters, 'all')
        query_end_time = time.time()

        logger.debug(
            "Query executed in %.2f seconds", query_end_time - query_start_time
        )

        method_end_time = time.time()
        logger.debug(
            "Completed fetch_random_movies in %.2f seconds",
            method_end_time - method_start_time,
        )

        return result

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

