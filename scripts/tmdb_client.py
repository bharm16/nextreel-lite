"""Async helper functions for interacting with The Movie Database (TMDb).

The :class:`TMDbHelper` class wraps common API calls such as retrieving movie
details, images and cast information.  The module is designed for educational
purposes and therefore contains inline comments explaining each step.
"""

import asyncio
import logging
from logging_config import get_logger
import os
import random
import time

import httpx
import tmdbsimple as tmdb

api_key = os.getenv("TMDB_API_KEY")
tmdb.API_KEY = api_key

# Hard coded API key and URL constants used by the helper functions.
TMDB_API_KEY = "1ce9398920594a5521f0d53e9b33c52f"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"

# Adjust working directory so relative imports (e.g. logging configuration) work
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)

logger = get_logger(__name__)


class TMDbHelper:
    """Lightweight async client for TMDb's REST API."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
        self.image_base_url = "https://image.tmdb.org/t/p/"

    async def _get(self, endpoint, params=None):
        """Internal helper to perform GET requests against TMDb."""

        if params is None:
            params = {}
        start_time = time.time()

        try:
            async with httpx.AsyncClient() as client:
                url = f"{self.base_url}/{endpoint}"
                params["api_key"] = self.api_key
                response = await client.get(url, params=params)
                response.raise_for_status()  # Raise for 4XX/5XX responses

                elapsed_time = time.time() - start_time
                logger.debug(
                    "Received response from %s in %.2f seconds. Status code: %s",
                    url,
                    elapsed_time,
                    response.status_code,
                )

                return response.json()
        except httpx.HTTPStatusError as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"HTTP error occurred while accessing {url}: {e}; Time elapsed: {elapsed_time:.2f} seconds"
            )
            raise
        except httpx.RequestError as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"Request error occurred while accessing {url}: {e}; Time elapsed: {elapsed_time:.2f} seconds"
            )
            raise
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"Unexpected error occurred while accessing {url}: {e}; Time elapsed: {elapsed_time:.2f} seconds"
            )
            raise

    async def get_cast_info_by_tmdb_id(self, tmdb_id):
        """Retrieve basic cast information for a given TMDb movie ID."""

        # Start timing for logging/debugging purposes
        start_time = time.time()
        try:
            data = await self._get(f"movie/{tmdb_id}/credits")
            cast_info = []
            for cast_member in data.get("cast", [])[
                :10
            ]:  # Limit to top 10 cast members
                profile_path = cast_member.get("profile_path")
                image_url = (
                    f"{self.image_base_url}w185{profile_path}" if profile_path else None
                )
                character_name = cast_member.get("character", "N/A")
                cast_info.append(
                    {
                        "name": cast_member["name"],
                        "image_url": image_url,
                        "character": character_name,
                    }
                )

            elapsed_time = time.time() - start_time
            logger.debug(
                "Successfully fetched and processed cast information for TMDB ID: %s in %.2f seconds",
                tmdb_id,
                elapsed_time,
            )

            return cast_info
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"Error fetching cast information for TMDB ID: {tmdb_id}. Error: {e}. Time elapsed: {elapsed_time:.2f} seconds"
            )
            raise

    async def get_video_url_by_tmdb_id(self, tmdb_id):
        """Return the first YouTube trailer URL for a movie, if available."""

        start_time = time.time()

        try:
            data = await self._get(f"movie/{tmdb_id}/videos")
            for video in data.get("results", []):
                if video["site"] == "YouTube" and video["type"] == "Trailer":
                    video_url = f"https://www.youtube.com/watch?v={video['key']}"
                    # logger.info(f"Found YouTube trailer for TMDB ID: {tmdb_id} - {video_url}")
                    return video_url

            # logger.warning(f"No YouTube trailer found for TMDB ID: {tmdb_id}")
            return None
        finally:
            elapsed_time = time.time() - start_time
            # logger.info(f"Completed fetching video URL for TMDB ID: {tmdb_id} in {elapsed_time:.2f} seconds")

    async def get_images_by_tmdb_id(self, tmdb_id, limit=1):
        """Fetch a limited number of poster and backdrop images."""
        start_time = time.time()
        try:
            data = await self._get(f"movie/{tmdb_id}/images")
            # Limit the number of posters and backdrops to fetch
            posters = data.get("posters", [])[:limit]
            backdrops = data.get("backdrops", [])[:limit]

            images = {
                "posters": [self.image_base_url + "original" + img["file_path"] for img in posters if
                            "file_path" in img],
                "backdrops": [self.image_base_url + "original" + img["file_path"] for img in backdrops if
                              "file_path" in img],
            }

            logger.debug(
                "Found %d poster(s) and %d backdrop(s) for TMDB ID: %s",
                len(images['posters']),
                len(images['backdrops']),
                tmdb_id,
            )
            return images
        finally:
            elapsed_time = time.time() - start_time
            logger.debug(
                "Completed fetching images for TMDB ID: %s in %.2f seconds",
                tmdb_id,
                elapsed_time,
            )

    async def get_videos_by_tmdb_id(self, tmdb_id):
        return await self._get(f"movie/{tmdb_id}/videos")

    async def get_credits_by_tmdb_id(self, tmdb_id):
        return await self._get(f"movie/{tmdb_id}/credits")

    async def get_tmdb_id_by_tconst(self, tconst):
        data = await self._get("find/" + tconst, {"external_source": "imdb_id"})
        return data["movie_results"][0]["id"] if data["movie_results"] else None

    async def get_movie_info_by_tmdb_id(self, tmdb_id):
        return await self._get(f"movie/{tmdb_id}")

    def get_full_image_url(self, profile_path, size="original"):
        return f"{self.image_base_url}{size}{profile_path}"

    async def get_backdrop_image_for_home(self, tmdb_id):
        image_data = await self.get_images_by_tmdb_id(tmdb_id)
        backdrops = image_data["backdrops"]
        if backdrops:
            return self.get_full_image_url(backdrops[0])
        return None

    async def get_all_backdrop_images(self, tmdb_id):
        image_data = await self.get_images_by_tmdb_id(tmdb_id)
        backdrops = image_data["backdrops"]
        return [self.get_full_image_url(backdrop) for backdrop in backdrops]

    async def get_backdrop_for_movie(self, tmdb_id):
        all_backdrop_urls = await self.get_all_backdrop_images(tmdb_id)
        return random.choice(all_backdrop_urls) if all_backdrop_urls else None


# Class for managing TMDb movie information
class TmdbMovieInfo:
    def __init__(self, api_key):
        self.api_key = api_key
        tmdb.API_KEY = self.api_key


async def main(api_key, tconst):
    tmdb_helper = TMDbHelper(api_key)

    tmdb_id = await tmdb_helper.get_tmdb_id_by_tconst(tconst)
    if tmdb_id:
        movie_info = await tmdb_helper.get_movie_info_by_tmdb_id(tmdb_id)
        print("Movie Information from TMDb:", movie_info)

        cast_info = await tmdb_helper.get_cast_info_by_tmdb_id(tmdb_id)
        print("Cast Information:")
        for cast_member in cast_info:
            print(f"{cast_member['name']} as {cast_member['character']}")
            if cast_member.get("image_url"):
                print(f"Image URL: {cast_member['image_url']}")
            else:
                print("Image not available")

        all_backdrops = await tmdb_helper.get_all_backdrop_images(tmdb_id)
        if all_backdrops:
            print("All backdrop images:")
            for backdrop in all_backdrops:
                print(backdrop)
        else:
            print("No backdrop images found.")
    else:
        print("TMDb ID not found.")


if __name__ == "__main__":
    api_key = (
        "1ce9398920594a5521f0d53e9b33c52f"  # Replace with your actual TMDb API key
    )
    tconst = "tt0111161"  # Replace with the IMDb tconst you have
    asyncio.run(main(api_key, tconst))
