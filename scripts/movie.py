import os
import random
import asyncio
import httpx

import config
from config import Config
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, execute_query
from scripts.tmdb_data import get_tmdb_id_by_tconst

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





class Find(TMDB):
    def __init__(self, api_key):
        super().__init__(api_key)

    async def by_imdb_id(self, imdb_id):
        """Asynchronously find a movie by IMDb ID."""
        async with httpx.AsyncClient() as client:
            tmdb_id = await get_tmdb_id_by_tconst(imdb_id, client)
            return tmdb_id




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
        self.tconst = tconst  # tconst is a string
        self.db_config = db_config
        self.movie_data = {}
        print(self.tconst)
        # self.numVotes = movie_data_from_db.get('numVotes', 'N/A')  # Safely extract numVotes
        self.db_config = db_config
        self.movie_data = {}

    # ... rest of your methods ...

    async def get_movie_data(self):
        find = Find(TMDB_API_KEY)
        movies = Movies(TMDB_API_KEY)

        tmdb_id = await find.by_imdb_id(self.tconst)

        if not tmdb_id:
            await find.close()
            return None

        movie_info = await movies.movie_info(tmdb_id)
        movie_credits = await movies.credits(tmdb_id)
        movie_images = await movies.images(tmdb_id)
        movie_videos = await movies.videos(tmdb_id)

        # Extracting director and writer names
        directors = [crew['name'] for crew in movie_credits.get('crew', []) if crew['job'] == 'Director']
        writers = [crew['name'] for crew in movie_credits.get('crew', []) if crew['job'] == 'Writer']

        # Fetch ratings from the IMDb database
        ratings_data = await fetch_movie_ratings(self.tconst)
        if ratings_data:
            self.movie_data["averageRating"] = ratings_data["averageRating"]
            self.movie_data["numVotes"] = ratings_data["numVotes"]
        else:
            self.movie_data["averageRating"] = 'N/A'
            self.movie_data["numVotes"] = 'N/A'

        # Forming movie data dictionary
        self.movie_data = {
            "title": movie_info.get('title', 'N/A'),
            "imdb_id": self.tconst,  # Using tconst as IMDb ID
            "tmdb_id": tmdb_id,  # Using TMDB ID instead of IMDb ID
            "genres": ', '.join([genre['name'] for genre in movie_info.get('genres', ['N/A'])]),
            "directors": ', '.join(directors),
            "writers": ', '.join(writers),
            "runtimes": movie_info.get('runtime', 'N/A'),
            "countries": ', '.join([country['name'] for country in movie_info.get('production_countries', ['N/A'])]),
            "languages": movie_info.get('original_language', 'N/A'),
            "rating": movie_info.get('vote_average', 'N/A'),
            # "votes": movie_info.get('vote_count', 'N/A'),
            "votes": self.movie_data.get('numVotes', 'N/A'),  # Use the numVotes from the fetched data

            "plot": movie_info.get('overview', 'N/A'),
            "poster_url": f"{TMDB_IMAGE_BASE_URL}w500{movie_info.get('poster_path')}" if movie_info.get(
                'poster_path') else None,
            "year": movie_info.get('release_date', 'N/A')[:4] if movie_info.get('release_date') else 'N/A',
            "cast": [{"name": cast['name'], "character": cast['character']} for cast in
                     movie_credits.get('cast', [])[:10]],  # Limit to 10
            "images": {
                "posters": [f"{TMDB_IMAGE_BASE_URL}w185{img['file_path']}" for img in movie_images.get('posters', [])],
                "backdrops": [f"{TMDB_IMAGE_BASE_URL}w185{img['file_path']}" for img in
                              movie_images.get('backdrops', [])]
            },
            "trailer": next(
                (f"https://www.youtube.com/watch?v={video['key']}" for video in movie_videos.get('results', []) if
                 video['site'] == 'YouTube' and video['type'] == 'Trailer'), None),
            "credits": {
                "cast": movie_credits.get('cast', []),
                "crew": movie_credits.get('crew', [])
            }
        }

        await find.close()
        await movies.close()
        return self.movie_data

    async def close(self):
        # Add any cleanup code here if necessary
        pass


# Main function with async execution
# Main function with async execution
# Main function with async execution
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


# Ensure asyncio.run is called if this script is the main one being run
if __name__ == "__main__":
    asyncio.run(main())
