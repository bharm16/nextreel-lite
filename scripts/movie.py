import os
import random
import asyncio
import httpx

import config
from scripts.set_filters_for_nextreel_backend import AsyncImdbRandomMovieFetcher

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


class Movie:
    def __init__(self, tconst, db_config):
        self.tconst = tconst
        self.db_config = db_config
        self.movie_data = {}

    async def fetch_info_from_imdb(self):
        # Implement this method using an async IMDb library or async HTTP requests
        pass

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
        await self.client.aclose()

# Main function with async execution
async def main(criteria):
    client = await get_http_client()
    movie_fetcher = AsyncImdbRandomMovieFetcher(config.Config.STACKHERO_DB_CONFIG)  # Must be an async version
    row = await movie_fetcher.fetch_random_movie(criteria)
    if not row:
        print("No movies found based on the given criteria.")
        return

    movie = Movie(row['tconst'], config.Config.STACKHERO_DB_CONFIG)
    movie_data = await movie.get_movie_data(client)
    print(movie_data)
    # ... rest of the main function ...

    await client.aclose()


# Entry point for the script
if __name__ == "__main__":
    criteria = {
        # ... criteria dictionary ...
    }
    asyncio.run(main(criteria))
