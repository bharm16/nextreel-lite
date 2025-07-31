import asyncio
import logging
import os
import time
import traceback
from typing import Any, Dict, List

from settings import Config, DatabaseConnectionPool
from db_utils import DatabaseQueryExecutor
from .interfaces import MovieFetcher

dbconfig = Config.get_db_config()

# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(filename)s - %(funcName)s - %(levelname)s - %(message)s'
)


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
            "AND tb.language LIKE %s"
        )

    @staticmethod
    def build_parameters(criteria: Dict[str, Any]) -> List[Any]:
        language = "%" + criteria.get("language", "en") + "%"
        return [
            criteria.get("min_year", 1900),
            criteria.get("max_year", 2023),
            criteria.get("min_rating", 7.0),
            criteria.get("max_rating", 10),
            criteria.get("min_votes", 100000),
            criteria.get("max_votes", 1000000),
            criteria.get("title_type", "movie"),
            language,
        ]

    @staticmethod
    def build_genre_conditions(criteria: Dict[str, Any], parameters: List[Any]) -> List[str]:
        genre_conditions: List[str] = []
        genres = criteria.get("genres")
        if genres:
            genre_conditions = [" OR ".join(["tb.genres LIKE %s" for _ in genres])]
            parameters.extend(["%" + genre + "%" for genre in genres])
        return genre_conditions


# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(filename)s - %(funcName)s - %(levelname)s - %(message)s'
)




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

            logging.info(f"Executing query with parameters: {parameters}")  # Improved logging

            result = await self.db_query_executor.execute_async_query(full_query, parameters, 'all')

            logging.info(f"Fetched {len(result)} movies by criteria in {time.time() - start_time:.2f} seconds")
            return result
        except Exception as e:
            logging.error(f"Error fetching movies by criteria: {e}\n{traceback.format_exc()}")
            raise

    async def fetch_random_movies(self, criteria, limit):
        """Fetch a random selection of movies matching the criteria."""
        method_start_time = time.time()

        logging.info(f"Starting fetch_random_movies with criteria: {criteria} and limit {limit}")

        base_query = MovieQueryBuilder.build_base_query()
        parameters = MovieQueryBuilder.build_parameters(criteria)

        logging.info(f"Parameters built: {parameters}")

        genre_conditions = MovieQueryBuilder.build_genre_conditions(criteria, parameters)

        if genre_conditions:
            logging.info(f"Genre conditions applied: {genre_conditions[0]}")

        full_query = base_query + (
            f" AND ({genre_conditions[0]})" if genre_conditions else "") + f" ORDER BY RAND() LIMIT {limit}"

        # Log the final query (optional, might be omitted for security/privacy reasons)
        # logging.debug(f"Executing query: {full_query}")

        # Time the query execution specifically
        query_start_time = time.time()
        result = await self.db_query_executor.execute_async_query(full_query, parameters, 'all')
        query_end_time = time.time()

        # Log the query execution time
        logging.info(f"Query executed in {query_end_time - query_start_time:.2f} seconds")

        # End timing the method execution
        method_end_time = time.time()

        # Log the total time taken by the method
        logging.info(f"Completed fetch_random_movies in {method_end_time - method_start_time:.2f} seconds")

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

    # Handling language criteria
    if form_data.get('language'):
        criteria['language'] = form_data.get('language')
    else:
        print("defaulting to english")
        criteria['language'] = 'en'  # Default to English

    return criteria


async def main():
    db_pool = DatabaseConnectionPool(dbconfig)
    await db_pool.init_pool()

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

    fetcher = ImdbRandomMovieFetcher(db_pool)
    movies = await fetcher.fetch_movies_by_criteria(criteria)

    for counter, movie in enumerate(movies, start=1):
        logging.info(f"Movie {counter}: {movie}")

    await db_pool.close_pool()  # Don't forget to close the pool at the end

if __name__ == "__main__":
    asyncio.run(main())

