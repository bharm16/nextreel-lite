import os
import random
import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import imdb

import config

from imdb import Cinemagoer

from config import Config
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, dbconfig

# Replace with your actual TMDb API key
TMDB_API_KEY = '1ce9398920594a5521f0d53e9b33c52f'
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"


# Async HTTP client setup
async def get_http_client():
    return httpx.AsyncClient()


# Async TMDb operations
async def get_tmdb_id_by_tconst(tconst, client):
    response = await client.get(
        f"{TMDB_API_BASE_URL}/find/{tconst}",
        params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"}
    )
    response.raise_for_status()
    data = response.json()
    return data['movie_results'][0]['id'] if data['movie_results'] else None


# Async function to get cast info by TMDb ID
async def get_cast_info_by_tmdb_id(tmdb_id, client):
    response = await client.get(
        f"{TMDB_API_BASE_URL}/movie/{tmdb_id}/credits",
        params={"api_key": TMDB_API_KEY}
    )
    response.raise_for_status()
    data = response.json()
    cast_info = []
    # Limit the number of cast members to the first 10
    for cast_member in data.get('cast', [])[:10]:
        profile_path = cast_member.get('profile_path')
        image_url = f"{TMDB_IMAGE_BASE_URL}w185{profile_path}" if profile_path else None
        # Add character name here
        character_name = cast_member.get('character', 'N/A')
        cast_info.append({
            'name': cast_member['name'],
            'image_url': image_url,
            'character': character_name  # Include the character name
        })
    return cast_info


# Async function to get video URL by TMDb ID
async def get_video_url_by_tmdb_id(tmdb_id, client):
    response = await client.get(
        f"{TMDB_API_BASE_URL}/movie/{tmdb_id}/videos",
        params={"api_key": TMDB_API_KEY}
    )
    response.raise_for_status()
    data = response.json()
    video_results = data.get('results', [])

    for video in video_results:
        # Only include videos that are on YouTube and are of type "Trailer"
        if video['site'] == 'YouTube' and video['type'] == 'Trailer':
            youtube_url = f"https://www.youtube.com/watch?v={video['key']}"
            return youtube_url  # Return the first suitable video URL found

    return None  # Return None if no suitable video is found


# Async function to fetch images from TMDb
async def fetch_images_from_tmdb(tmdb_id, client):
    """
    Fetch movie images from TMDb using the movie's TMDb ID.
    """
    response = await client.get(
        f"{TMDB_API_BASE_URL}/movie/{tmdb_id}/images",
        params={"api_key": TMDB_API_KEY}
    )
    response.raise_for_status()
    data = response.json()
    image_data = {
        'posters': [img['file_path'] for img in data.get('posters', [])],
        'backdrops': [img['file_path'] for img in data.get('backdrops', [])]
    }
    return image_data


# Async function to fetch videos from TMDb
async def fetch_videos_from_tmdb(tmdb_id, client):
    """
    Fetch movie videos from TMDb using the movie's TMDb ID.
    """
    response = await client.get(
        f"{TMDB_API_BASE_URL}/movie/{tmdb_id}/videos",
        params={"api_key": TMDB_API_KEY}
    )
    response.raise_for_status()
    data = response.json()
    return data.get('results', [])


# Async function to get credits by TMDb ID
async def get_credits_by_tmdb_id(tmdb_id, client):
    """
    Fetch the cast and crew for a movie using its TMDb ID.
    """
    response = await client.get(
        f"{TMDB_API_BASE_URL}/movie/{tmdb_id}/credits",
        params={"api_key": TMDB_API_KEY}
    )
    response.raise_for_status()
    data = response.json()
    return {
        'cast': data.get('cast', []),
        'crew': data.get('crew', [])
    }


def get_movie(imdbId):
    """Blocking function that gets movie data from IMDb."""
    ia = imdb.Cinemagoer()
    # Ensure imdbId is a string before passing to get_movie
    movie_data = ia.get_movie(str(imdbId))
    return movie_data


class Movie:
    def __init__(self, tconst, db_config, client):
        self.tconst = tconst
        self.db_config = db_config
        self.client = client  # Assign the passed client to self.client

        self.movie_data = {}
        self.executor = ThreadPoolExecutor()  # This will run blocking calls in separate threads
        self.ia = Cinemagoer()  # Create an instance of the Cinemagoer class

    async def fetch_info_from_imdb(self):
        """Fetch movie information from IMDb using an async wrapper."""
        imdbId = int(self.tconst[2:])
        # Run the blocking function get_movie in the executor
        movie_data = await asyncio.get_event_loop().run_in_executor(
            self.executor, get_movie, imdbId
        )
        return movie_data

    async def store_movie_data(self, movie):
        """
        Store movie data from IMDb and TMDb sources into a single dictionary.

        Args:
            movie (dict): Movie data dictionary.
        """
        tmdb_id = await get_tmdb_id_by_tconst(self.tconst, self.client)
        tmdb_cast_info = []
        tmdb_image_info = {}
        tmdb_movie_trailer = None
        tmdb_credits = {}

        # Check if there is a TMDb ID corresponding to the IMDb ID
        if tmdb_id:
            # Fetch cast and crew information from TMDb asynchronously
            tmdb_credits = await get_credits_by_tmdb_id(tmdb_id, self.client)
            tmdb_movie_trailer = await get_video_url_by_tmdb_id(tmdb_id, self.client)
            tmdb_cast_info = await get_cast_info_by_tmdb_id(tmdb_id, self.client)
            tmdb_image_info = await fetch_images_from_tmdb(tmdb_id, self.client)

        # Populate the movie_data dictionary with various fields
        self.movie_data = {
            "title": movie.get('title', 'N/A'),
            "imdb_id": movie.getID(),
            "genres": ', '.join(movie.get('genres', ['N/A'])),
            "directors": ', '.join([director['name'] for director in movie.get('director', [])]),
            "writers": next((writer['name'] for writer in movie.get('writer', []) if 'name' in writer), "N/A"),
            "runtimes": ', '.join(movie.get('runtimes', ['N/A'])),
            "countries": ', '.join(movie.get('countries', ['N/A'])),
            "languages": movie.get('languages', ['N/A'])[0] if movie.get('languages') else 'N/A',
            "rating": movie.get('rating', 'N/A'),
            "votes": movie.get('votes', 'N/A'),
            "plot": movie.get('plot', ['N/A'])[0],
            "poster_url": movie.get_fullsizeURL(),
            "year": movie.get('year'),
            "cast": tmdb_cast_info,  # This now includes character names
            "images": tmdb_image_info,  # Add TMDb image information
            "trailer": tmdb_movie_trailer,  # Add TMDb video URLs
            "credits": tmdb_credits  # Add TMDb credits information
        }

    async def get_movie_data(self):
        movie_data_imdb = await self.fetch_info_from_imdb()
        await self.store_movie_data(movie_data_imdb)
        return self.movie_data

    async def close(self):
        self.executor.shutdown(wait=True)



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

    # Create an instance of the HTTP client
    async with httpx.AsyncClient() as client:
        # Instantiate your movie fetcher class
        fetcher = ImdbRandomMovieFetcher(db_config)

        # Fetch a random movie based on criteria
        row = await fetcher.fetch_random_movie(criteria)

        if not row:
            print("No movies found based on the given criteria.")
            return

        # Pass the client to the Movie constructor
        movie = Movie(row['tconst'], db_config, client)
        movie_data = await movie.get_movie_data()

        # Print movie data
        print(movie_data)

        # Iterate through posters
        for image in movie_data["images"].get('posters', []):
            print(image)

        # Print cast information
        print("\nCast Information:")
        for cast_member in movie_data.get("cast", []):
            print(f"Name: {cast_member['name']}, Image URL: {cast_member['image_url']}")

        # Print trailers
        print("\nTrailers:")
        print(movie_data.get("trailer", []))

        # Print additional cast information
        print("\nCast Information:")
        for cast_member in movie_data.get("cast", []):
            print(f"Name: {cast_member['name']}, Character: {cast_member['character']}, Image URL: {cast_member['image_url']}")

# Ensure asyncio.run is called if this script is the main one being run
if __name__ == "__main__":
    asyncio.run(main())
