import os
from config import Config, create_aiomysql_connection

dbconfig = Config.STACKHERO_DB_CONFIG

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)


def build_parameters(criteria):
    language = "%" + criteria.get('language', 'en') + "%"
    parameters = [
        criteria.get('min_year', 1900),
        criteria.get('max_year', 2023),
        criteria.get('min_rating', 7.0),
        criteria.get('max_rating', 10),
        criteria.get('min_votes', 100000),
        criteria.get('title_type', 'movie'),
        language
    ]
    return parameters


def build_genre_conditions(criteria, parameters):
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
    AND tb.language LIKE %s
    """


def build_ratings_query():
    return """
    SELECT tr.tconst, tr.averageRating, tr.numVotes
    FROM `title.ratings` tr
    WHERE tr.tconst = %s
    """


async def execute_query(query, params=None, fetch='one'):
    conn = await create_aiomysql_connection()
    if not conn:
        print("Failed to establish database connection.")
        return None

    try:
        async with conn.cursor() as cursor:
            await cursor.execute(query, params)
            if fetch == 'one':
                result = await cursor.fetchone()
            elif fetch == 'all':
                result = await cursor.fetchall()
            elif fetch == 'none':
                await conn.commit()
                result = None
            else:
                raise ValueError(f"Invalid fetch parameter: {fetch}")
        return result
    except Exception as e:
        print(f"An error occurred while executing the query: {e}")
        return None
    finally:
        conn.close()





class ImdbRandomMovieFetcher:
    def __init__(self, dbconfig):
        self.dbconfig = dbconfig
        self.last_fetched_movies = None
        self.last_fetched_random_movies = None

    async def fetch_movie_ratings(self, tconst):
        query = build_ratings_query()
        result = await execute_query(query, [tconst], fetch='one')

        if result:
            try:
                # Accessing result as a dictionary
                ratings_data = {
                    "tconst": result['tconst'],
                    "averageRating": result['averageRating'] if result['averageRating'] is not None else 'N/A',
                    "numVotes": result['numVotes'] if result['numVotes'] is not None else 'N/A'
                }
                return ratings_data
            except KeyError as e:
                print(f"Error in fetch_movie_ratings: {e}")
                print(f"Result missing expected key: {result}")
                return None
        else:
            print(f"No ratings found for tconst: {tconst}")
            return None

    async def fetch_movies_by_criteria(self, criteria):
        base_query = build_base_query()
        parameters = build_parameters(criteria)
        genre_conditions = build_genre_conditions(criteria, parameters)
        full_query = base_query + (f" AND ({genre_conditions[0]})" if genre_conditions else "")
        result = await execute_query(full_query, parameters, 'all')
        if result:
            self.last_fetched_movies = [await self.fetch_movie_ratings(movie['tconst']) for movie in result]
            print(self.last_fetched_movies)

        return result

    async def fetch_random_movies25(self, criteria, client):
        base_query = build_base_query()
        parameters = build_parameters(criteria)
        genre_conditions = build_genre_conditions(criteria, parameters)
        full_query = base_query + (
            f" AND ({genre_conditions[0]})" if genre_conditions else "") + " ORDER BY RAND() LIMIT 15"
        result = await execute_query(full_query, parameters, 'all')
        if result:
            self.last_fetched_random_movies = [await self.fetch_movie_ratings(movie['tconst']) for movie in result]
            print(self.last_fetched_movies)

        return result

    async def fetch_random_movie(self, criteria, client):
        base_query = build_base_query()
        parameters = build_parameters(criteria)
        genre_conditions = build_genre_conditions(criteria, parameters)
        full_query = base_query + (
            f" AND ({genre_conditions[0]})" if genre_conditions else "") + " ORDER BY RAND() LIMIT 1"
        result = await execute_query(full_query, parameters)
        if result:
            self.last_fetched_movie = await self.fetch_movie_ratings(result['tconst'])
            print(self.last_fetched_movie)
        return self.last_fetched_movie


def extract_movie_filter_criteria(form_data):
    criteria = {}
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
    genres = form_data.getlist('genres[]')
    if genres:
        criteria['genres'] = genres
    criteria['language'] = form_data.get('language', 'en')
    return criteria


async def main():
    criteria = {'min_year': 2000, 'max_year': 2020, 'min_rating': 7, 'max_rating': 10, 'min_votes': 10000,
                'title_type': 'movie', 'language': 'en', 'genres': ['Action', 'Drama']}
    dbconfig = Config.STACKHERO_DB_CONFIG
    fetcher = ImdbRandomMovieFetcher(dbconfig)
    await fetcher.fetch_movies_by_criteria(criteria)






if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
