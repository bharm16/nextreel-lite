import asyncio
import logging
import os

import httpx

from config import Config
from mysql_query_builder import DatabaseQueryExecutor
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher
from scripts.tmdb_data import TMDbHelper

# Configure logging for better debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(filename)s - %(funcName)s - %(levelname)s - %(message)s'
)
# Set httpx logging level to ERROR to reduce verbosity
logging.getLogger("httpx").setLevel(logging.ERROR)

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)


def build_ratings_query():
    return """
    SELECT tr.tconst, tr.averageRating, tr.numVotes
    FROM `title.ratings` tr
    WHERE tr.tconst = %s
    """


# Replace with your actual TMDb API key
TMDB_API_KEY = '1ce9398920594a5521f0d53e9b33c52f'
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"

# Replace with your actual TMDb API key
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"


# Async HTTP client setup


# Async TMDb operations
async def get_tmdb_id_by_tconst(tconst, client):
    response = await client.get(
        f"{TMDB_API_BASE_URL}/find/{tconst}",
        params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"}
    )
    response.raise_for_status()
    data = response.json()
    return data['movie_results'][0]['id'] if data['movie_results'] else None


class TMDB:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key):
        self.api_key = api_key
        self.client = httpx.AsyncClient()  # Initialize the HTTP client

    async def _GET(self, path, params={}):
        """Send an asynchronous GET request to the TMDB API."""
        params['api_key'] = self.api_key
        response = await self.client.get(f"{self.BASE_URL}/{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def get_movie_by_tmdb_id(self, tmdb_id):
        """Get a movie by its TMDb ID."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.BASE_URL}/movie/{tmdb_id}", params={"api_key": self.api_key})
            response.raise_for_status()
            return response.json()

    async def fetch_movie_details(self, tmdb_id):
        try:
            movie_details = await self.get_movie_by_tmdb_id(tmdb_id)
            return movie_details
        except Exception as e:
            print(f"An error occurred: {e}")
            return None

        # Make sure to close the client when it's no longer needed

    async def close(self):
        await self.client.aclose()


class Movie:
    def __init__(self, tconst, db_config):
        self.tconst = tconst
        self.db_config = db_config
        self.movie_data = {}
        self.query_executor = DatabaseQueryExecutor(db_config)  # Corrected here
        self.tmdb_helper = TMDbHelper(TMDB_API_KEY)  # Initialize TMDbHelper

    async def fetch_movie_ratings(self, tconst):
        query = build_ratings_query()
        result = await self.query_executor.execute_async_query(query, [tconst], fetch='one')

        if result:
            try:
                # Accessing result as a dictionary
                ratings_data = {
                    "tconst": result['tconst'],
                    "averageRating": result['averageRating'] if result['averageRating'] is not None else 'N/A',
                    "numVotes": result['numVotes'] if result['numVotes'] is not None else 'N/A'

                }
                print(ratings_data)
                return ratings_data
            except KeyError as e:
                print(f"Error in fetch_movie_ratings: {e}")
                print(f"Result missing expected key: {result}")
                return None
        else:
            print(f"No ratings found for tconst: {tconst}")
            return None

    async def get_movie_data(self):
        tmdb_id = await self.tmdb_helper.get_tmdb_id_by_tconst(self.tconst)

        ratings_data = await self.fetch_movie_ratings(self.tconst)


        if not tmdb_id:
            return None

        movie_info = await self.tmdb_helper.get_movie_info_by_tmdb_id(tmdb_id)
        tmdb_credits = await self.tmdb_helper.get_credits_by_tmdb_id(tmdb_id)
        tmdb_movie_trailer = await self.tmdb_helper.get_video_url_by_tmdb_id(tmdb_id)
        tmdb_cast_info_result = await self.tmdb_helper.get_cast_info_by_tmdb_id(tmdb_id)
        tmdb_cast_info = tmdb_cast_info_result[:10] if tmdb_cast_info_result else []  # Limit to 10 cast members
        tmdb_image_info = await self.tmdb_helper.get_images_by_tmdb_id(tmdb_id)

        backdrop_url = tmdb_image_info['backdrops'][0] if tmdb_image_info.get('backdrops') else None
        # print(backdrop_url)

        # Custom logging for title, tconst, and backdrop image
        # logging.info(f"Title: {movie_info.get('title', 'N/A')}, tconst: {self.tconst}, Backdrop URL: {backdrop_url}")

        # Assuming movie_info has a 'vote_average' key for the rating
        logging.info(
            f"Title: {movie_info.get('title', 'N/A')}, tconst: {self.tconst}, Rating: {movie_info.get('vote_average', 'N/A')}")

        # Use database rating if available; otherwise, fall back to TMDB rating
        if ratings_data and ratings_data["averageRating"] != 'N/A':
            rating = ratings_data["averageRating"]
            votes = ratings_data["numVotes"]
        else:
            rating = movie_info.get('vote_average', 'N/A')
            votes = movie_info.get('vote_count', 'N/A')
        directors = [crew['name'] for crew in tmdb_credits.get('crew', []) if crew['job'] == 'Director']
        writers = [crew['name'] for crew in tmdb_credits.get('crew', []) if crew['job'] == 'Writer']

        self.movie_data = {
            "title": movie_info.get('title', 'N/A'),
            "imdb_id": self.tconst,
            "tmdb_id": tmdb_id,
            "genres": ', '.join([genre['name'] for genre in movie_info.get('genres', ['N/A'])]),
            "directors": ', '.join(directors),
            "writers": ', '.join(writers),
            "runtimes": movie_info.get('runtime', 'N/A'),
            "countries": ', '.join([country['name'] for country in movie_info.get('production_countries', ['N/A'])]),
            "languages": movie_info.get('original_language', 'N/A'),
            "rating": rating,
            "votes": votes,
            "plot": movie_info.get('overview', 'N/A'),
            "poster_url": f"{TMDB_IMAGE_BASE_URL}w500{movie_info.get('poster_path')}" if movie_info.get(
                'poster_path') else None,
            "year": movie_info.get('release_date', 'N/A')[:4] if movie_info.get('release_date') else 'N/A',
            "cast": tmdb_cast_info,
            "images": tmdb_image_info,
            "trailer": tmdb_movie_trailer,
            "credits": tmdb_credits,
            "backdrop_url": backdrop_url,  # Add backdrop URL here

        }

        return self.movie_data

    async def close(self):
        pass


async def main():
    db_config = Config.STACKHERO_DB_CONFIG  # Assuming you have a db_config defined

    tconst = 'tt0988045'  # Example IMDb ID
    db_config = Config.STACKHERO_DB_CONFIG  # Your database configuration
    movie_instance = Movie(tconst, db_config)
    movie_data = await movie_instance.get_movie_data()
    if movie_data:
        print(f"Movie Data: {movie_data}")
    else:
        print("Failed to fetch movie data.")




if __name__ == "__main__":
    asyncio.run(main())
