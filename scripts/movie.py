import os
import random
import asyncio
import httpx
from tmdbsimple import find

import config
from config import Config
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, execute_query
from scripts.tmdb_data import fetch_images_from_tmdb, get_movie_info_by_tmdb_id, fetch_videos_from_tmdb, \
    get_credits_by_tmdb_id, get_video_url_by_tmdb_id, get_cast_info_by_tmdb_id

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)


def build_ratings_query():
    return """
    SELECT tr.tconst, tr.averageRating, tr.numVotes
    FROM `title.ratings` tr
    WHERE tr.tconst = %s
    """


async def fetch_movie_ratings(tconst):
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


async def by_imdb_id(imdb_id):
    """Asynchronously find a movie by IMDb ID."""
    async with httpx.AsyncClient() as client:
        tmdb_id = await get_tmdb_id_by_tconst(imdb_id, client)
        return tmdb_id


class Find(TMDB):
    def __init__(self, api_key):
        super().__init__(api_key)


class Movies(TMDB):
    def __init__(self, api_key):
        super().__init__(api_key)

    async def movie_info(self, tmdb_id):
        """Get information about a movie by its TMDB ID."""
        return await self._GET(f"movie/{tmdb_id}")

    async def credits(self, tmdb_id):
        """Get credits for the movie."""
        return await self._GET(f"movie/{tmdb_id}/credits")

    async def images(self, tmdb_id):
        """Get images for the movie."""
        return await self._GET(f"movie/{tmdb_id}/images")

    async def videos(self, tmdb_id):
        """Get videos for the movie."""
        return await self._GET(f"movie/{tmdb_id}/videos")


class Movie:
    def __init__(self, tconst, db_config):
        self.tconst = tconst
        self.db_config = db_config
        self.movie_data = {}

    async def get_movie_data(self):
        async with httpx.AsyncClient() as client:
            tmdb_id = await get_tmdb_id_by_tconst(self.tconst, client)

            # if not tmdb_id:
            #     return None

            # Fetch cast and crew information from TMDb
            tmdb_credits = await get_credits_by_tmdb_id(tmdb_id, client)

            # Fetch a trailer URL from TMDb
            tmdb_movie_trailer = await get_video_url_by_tmdb_id(tmdb_id, client)

            tmdb_cast_info_result = await get_cast_info_by_tmdb_id(tmdb_id, client)
            tmdb_cast_info = tmdb_cast_info_result[:10] if tmdb_cast_info_result else []

            # Fetch image information from TMDb
            tmdb_image_info = await fetch_images_from_tmdb(tmdb_id, client)

            # Fetch additional movie details
            movie_info = await get_movie_info_by_tmdb_id(tmdb_id, client)

            # Fetch ratings from the IMDb database
            ratings_data = await fetch_movie_ratings(self.tconst)
            if ratings_data:
                self.movie_data["averageRating"] = ratings_data["averageRating"]
                self.movie_data["numVotes"] = ratings_data["numVotes"]
            else:
                self.movie_data["averageRating"] = 'N/A'
                self.movie_data["numVotes"] = 'N/A'


            directors = [crew['name'] for crew in tmdb_credits.get('crew', []) if crew['job'] == 'Director']
            writers = [crew['name'] for crew in tmdb_credits.get('crew', []) if crew['job'] == 'Writer']


            # Forming movie data dictionary
            self.movie_data = {
                "title": movie_info.get('title', 'N/A'),
                "imdb_id": self.tconst,
                "tmdb_id": tmdb_id,
                "genres": ', '.join([genre['name'] for genre in movie_info.get('genres', ['N/A'])]),
                "directors": ', '.join([crew['name'] for crew in tmdb_credits.get('crew', []) if crew['job'] == 'Director']),
                "writers": ', '.join([crew['name'] for crew in tmdb_credits.get('crew', []) if crew['job'] == 'Writer']),
                "runtimes": movie_info.get('runtime', 'N/A'),
                "countries": ', '.join([country['name'] for country in movie_info.get('production_countries', ['N/A'])]),
                "languages": movie_info.get('original_language', 'N/A'),
                "rating": movie_info.get('vote_average', 'N/A'),
                "votes": self.movie_data.get('numVotes', 'N/A'),
                "plot": movie_info.get('overview', 'N/A'),
                "poster_url": f"{TMDB_IMAGE_BASE_URL}w500{movie_info.get('poster_path')}" if movie_info.get('poster_path') else None,
                "year": movie_info.get('release_date', 'N/A')[:4] if movie_info.get('release_date') else 'N/A',
                "cast": tmdb_cast_info,
                "images": tmdb_image_info,
                "trailer": tmdb_movie_trailer,
                "credits": tmdb_credits
            }

            return self.movie_data

    async def close(self):
        pass

# Continue with the main() function and other parts of the script



async def main():
    db_config = Config.STACKHERO_DB_CONFIG  # Assuming you have a db_config defined

    # Define criteria for movie selection
    criteria = {
        "min_year": 1900,
        "max_year": 2023,
        "min_rating": 7.0,
        "max_rating": 10,
        "title_type": "movie",
        "language": "en",
        "genres": ["Action", "Drama"]
    }

    async with httpx.AsyncClient() as client:
        fetcher = ImdbRandomMovieFetcher(db_config)
        movie_data_from_db = await fetcher.fetch_random_movie(criteria, client)

        if not movie_data_from_db:
            print("No movies found based on the given criteria.")
            return

        # Assuming movie_data_from_db contains 'tconst' key
        if 'tconst' in movie_data_from_db:
            tconst = movie_data_from_db['tconst']
            movie = Movie(tconst, db_config)
            movie_data = await movie.get_movie_data()
            print(movie_data)
        else:
            print("Tconst not found in movie data.")


if __name__ == "__main__":
    asyncio.run(main())

