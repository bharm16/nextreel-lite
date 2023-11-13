import os
import random
import asyncio
import httpx

import config
from config import Config
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher

# Replace with your actual TMDb API key
TMDB_API_KEY = '1ce9398920594a5521f0d53e9b33c52f'
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"


class TMDB:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key):
        self.api_key = api_key
        self.client = httpx.AsyncClient()

    async def _GET(self, path, params={}):
        """Send an asynchronous GET request to the TMDB API."""
        params['api_key'] = self.api_key
        response = await self.client.get(f"{self.BASE_URL}/{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()


class Find(TMDB):
    def __init__(self, api_key):
        super().__init__(api_key)

    async def by_imdb_id(self, imdb_id):
        """Asynchronously find a movie by IMDb ID."""
        return await self._GET(f"find/{imdb_id}", {"external_source": "imdb_id"})


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


class Movies(TMDB):
    def __init__(self, api_key):
        super().__init__(api_key)

    def movie_info(self, tmdb_id):
        """Get information about a movie by its TMDB ID."""
        response = self._GET(f"movie/{tmdb_id}")
        return response

    def credits(self, tmdb_id):
        """Get credits for the movie."""
        response = self._GET(f"movie/{tmdb_id}/credits")
        return response

    def images(self, tmdb_id):
        """Get images for the movie."""
        response = self._GET(f"movie/{tmdb_id}/images")
        return response

    def videos(self, tmdb_id):
        """Get videos for the movie."""
        response = self._GET(f"movie/{tmdb_id}/videos")
        return response


class Movie:
    def __init__(self, tconst, db_config):
        self.tconst = tconst
        self.db_config = db_config
        self.movie_data = {}

    async def get_movie_data(self):
        find = Find(TMDB_API_KEY)
        movies = Movies(TMDB_API_KEY)

        tmdb_find_result = await find.by_imdb_id(self.tconst)
        tmdb_id = tmdb_find_result["movie_results"][0]["id"] if tmdb_find_result["movie_results"] else None

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
            "votes": movie_info.get('vote_count', 'N/A'),
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
        row = await fetcher.fetch_random_movie(criteria, client)

        if not row:
            print("No movies found based on the given criteria.")
            return

        movie = Movie(row['tconst'], db_config)
        movie_data = await movie.get_movie_data()
        print(movie_data)


# Ensure asyncio.run is called if this script is the main one being run
if __name__ == "__main__":
    asyncio.run(main())

# Ensure asyncio.run is called if this script is the main one being run
if __name__ == "__main__":
    asyncio.run(main())
