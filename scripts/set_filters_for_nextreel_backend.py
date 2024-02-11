import logging
import os

from config import Config, DatabaseConnection
from mysql_query_builder import DatabaseQueryExecutor

dbconfig = Config.STACKHERO_DB_CONFIG

# Use os.path.dirname to go up one level from the current script's directory
# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(filename)s - %(funcName)s - %(levelname)s - %(message)s'
)

# Finally, print the new working directory to confirm the change
# print(f"Current working directory after change: {os.getcwd()}")


def build_parameters(criteria):
    """Construct the list of parameters for the SQL query based on given criteria."""
    # Note: Added "LIKE" clause for the language
    language = "%" + criteria.get('language', 'en') + "%"
    parameters = [
        criteria.get('min_year', 1900),
        criteria.get('max_year', 2023),
        criteria.get('min_rating', 7.0),
        criteria.get('max_rating', 10),
        criteria.get('min_votes', 100000),
        criteria.get('title_type', 'movie'),
        language  # added this line
    ]
    return parameters


def build_genre_conditions(criteria, parameters):
    """Construct the genre conditions for the SQL query."""
    genre_conditions = []
    genres = criteria.get('genres')
    if genres:
        genre_conditions = [" OR ".join(["tb.genres LIKE %s" for _ in genres])]
        parameters.extend(["%" + genre + "%" for genre in genres])
    return genre_conditions


def build_base_query():
    return """
    SELECT tb.*
    FROM `title.basics` tb
    JOIN `title.ratings` tr ON tb.tconst = tr.tconst
    WHERE tb.startYear BETWEEN %s AND %s
    AND tr.averagerating BETWEEN %s AND %s
    AND tr.numVotes >= %s
    AND tb.titleType = %s
    AND tb.language LIKE %s  -- Changed this line
    """


db_connection = DatabaseConnection(Config.STACKHERO_DB_CONFIG)


# Modify the execute_query function to use the DatabaseConnection


# Convert the ImdbRandomMovieFetcher class methods to async
class ImdbRandomMovieFetcher:
    def __init__(self, dbconfig):
        self.dbconfig = dbconfig
        self.db_query_executor = DatabaseQueryExecutor(dbconfig)  # Use dbconfig directly

    async def fetch_movies_by_criteria(self, criteria):
        base_query = build_base_query()
        parameters = build_parameters(criteria)
        genre_conditions = build_genre_conditions(criteria, parameters)
        full_query = base_query + (f" AND ({genre_conditions[0]})" if genre_conditions else "")
        return await self.db_query_executor.execute_async_query(full_query, parameters, 'all')

    async def fetch_random_movies15(self, criteria):
        base_query = build_base_query()
        parameters = build_parameters(criteria)
        genre_conditions = build_genre_conditions(criteria, parameters)
        full_query = base_query + (
            f" AND ({genre_conditions[0]})" if genre_conditions else "") + " ORDER BY RAND() LIMIT 15"
        return await self.db_query_executor.execute_async_query(full_query, parameters, 'all')

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
    criteria = {'min_year': 2000,
                'max_year': 2020,
                'min_rating': 7.0,
                'max_rating': 10,
                'min_votes': 10000,
                'title_type': 'movie',
                'language': 'en',
                'genres': ['Action', 'Drama']}

    dbconfig = Config.STACKHERO_DB_CONFIG
    fetcher = ImdbRandomMovieFetcher(dbconfig)
    movies = await fetcher.fetch_movies_by_criteria(criteria)

    # Iterate over the movies and print each movie on a new line with a counter
    for counter, movie in enumerate(movies, start=1):  # start=1 begins the counter at 1
        print(f"Movie {counter}: {movie}")  # This will print the movie number and its details


# Example usage
if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
