import asyncio
import logging
from logging_config import get_logger
import os
import time
"""High level abstraction representing a movie and its metadata sources."""

import os
import time
import logging
import httpx

from logging_config import get_logger
from settings import Config, DatabaseConnectionPool
from db_utils import DatabaseQueryExecutor
from scripts.filter_backend import ImdbRandomMovieFetcher
from scripts.tmdb_client import TMDbHelper

# Configure logging for better debugging
logger = get_logger(__name__)
# Set httpx logging level to ERROR to reduce verbosity
logging.getLogger("httpx").setLevel(logging.ERROR)

# Ensure relative paths resolve correctly when this module is executed directly
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)


def build_ratings_query():
    """SQL snippet used to fetch rating information for a given movie."""

    return """
    SELECT tr.tconst, tr.averageRating, tr.numVotes
    FROM `title.ratings` tr
    WHERE tr.tconst = %s
    """


# Replace with your actual TMDb API key
TMDB_API_KEY = "1ce9398920594a5521f0d53e9b33c52f"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"

# Replace with your actual TMDb API key
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"


# Async HTTP client setup


# Async TMDb operations
async def get_tmdb_id_by_tconst(tconst, client):
    response = await client.get(
        f"{TMDB_API_BASE_URL}/find/{tconst}",
        params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
    )
    response.raise_for_status()
    data = response.json()
    return data["movie_results"][0]["id"] if data["movie_results"] else None


class TMDB:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key):
        self.api_key = api_key
        self.client = httpx.AsyncClient()  # Initialize the HTTP client

    async def _GET(self, path, params=None):
        """Send an asynchronous GET request to the TMDB API."""
        if params is None:
            params = {}
        params["api_key"] = self.api_key
        response = await self.client.get(f"{self.BASE_URL}/{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def get_movie_by_tmdb_id(self, tmdb_id):
        """Get a movie by its TMDb ID."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/movie/{tmdb_id}", params={"api_key": self.api_key}
            )
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
    """Encapsulates retrieval and aggregation of movie metadata."""

    def __init__(self, tconst, db_pool):
        self.tconst = tconst
        self.db_pool = db_pool
        self.movie_data = {}
        self.query_executor = DatabaseQueryExecutor(db_pool)
        self.tmdb_helper = TMDbHelper(TMDB_API_KEY)
        self.slug = None  # Populated from the database if available
        self.client = httpx.AsyncClient()  # Reused for TMDb lookups

    async def fetch_movie_slug(self):
        """Populate ``self.slug`` from the database if available."""

        start_time = time.time()
        query = """
           SELECT slug FROM `title.basics` WHERE tconst = %s;
           """
        result = await self.query_executor.execute_async_query(
            query, [self.tconst], fetch="one"
        )
        if result:
            self.slug = result["slug"]
            logger.info(f"Slug for tconst {self.tconst}: {self.slug}")
        else:
            logger.warning(f"No slug found for tconst: {self.tconst}")

        method_time = time.time() - start_time
        logger.info(
            f"Completed fetch_movie_slug for {self.tconst} in {method_time:.2f} seconds."
        )

    # Assume the necessary imports and setup for logging are done elsewhere in your code

    async def fetch_movie_ratings(self, tconst):
        """Retrieve rating information from the local database."""

        start_time = time.time()

        query = build_ratings_query()
        result = await self.query_executor.execute_async_query(
            query, [tconst], fetch="one"
        )

        if result:
            try:
                ratings_data = {
                    "tconst": result["tconst"],
                    "averageRating": (
                        result["averageRating"]
                        if result["averageRating"] is not None
                        else "N/A"
                    ),
                    "numVotes": (
                        result["numVotes"] if result["numVotes"] is not None else "N/A"
                    ),
                }

                logger.info(f"Ratings data: {ratings_data}")

                query_time = time.time() - start_time
                logger.info(f"Fetched movie ratings in {query_time:.2f} seconds")

                return ratings_data

            except KeyError as e:
                logger.error(f"Error in fetch_movie_ratings: {e}")
                logger.error(f"Result missing expected key: {result}")
                return None
        else:
            logger.info(f"No ratings found for tconst: {tconst}")
            return None

    async def get_movie_data(self):
        """Aggregate TMDb and local metadata into ``self.movie_data``."""

        start_time = time.time()

        tmdb_id = await self.tmdb_helper.get_tmdb_id_by_tconst(self.tconst)
        await self.fetch_movie_slug()

        if not tmdb_id:
            logger.warning(f"No TMDB ID found for tconst: {self.tconst}")
            return None

        tasks = [
            self.tmdb_helper.get_movie_info_by_tmdb_id(tmdb_id),
            self.tmdb_helper.get_credits_by_tmdb_id(tmdb_id),
            self.tmdb_helper.get_video_url_by_tmdb_id(tmdb_id),
            self.tmdb_helper.get_cast_info_by_tmdb_id(tmdb_id),
            self.tmdb_helper.get_images_by_tmdb_id(tmdb_id),
            self.fetch_movie_ratings(self.tconst),
        ]
        (
            movie_info,
            tmdb_credits,
            tmdb_movie_trailer,
            tmdb_cast_info_result,
            tmdb_image_info,
            ratings_data,
        ) = await asyncio.gather(*tasks)

        tmdb_cast_info = (
            tmdb_cast_info_result[:10] if tmdb_cast_info_result else []
        )

        backdrop_url = (
            tmdb_image_info["backdrops"][0]
            if tmdb_image_info.get("backdrops")
            else None
        )

        rating = (
            ratings_data["averageRating"]
            if ratings_data and ratings_data["averageRating"] != "N/A"
            else movie_info.get("vote_average", "N/A")
        )
        votes = (
            ratings_data["numVotes"]
            if ratings_data and ratings_data["numVotes"] != "N/A"
            else movie_info.get("vote_count", "N/A")
        )

        directors = [
            crew["name"]
            for crew in tmdb_credits.get("crew", [])
            if crew["job"] == "Director"
        ]
        writers = [
            crew["name"]
            for crew in tmdb_credits.get("crew", [])
            if crew["job"] == "Writer"
        ]

        self.movie_data = {
            "title": movie_info.get("title", "N/A"),
            "imdb_id": self.tconst,
            "tmdb_id": tmdb_id,
            "genres": ", ".join(
                [genre["name"] for genre in movie_info.get("genres", [])]
            ),
            "directors": ", ".join(directors),
            "rating": rating,
            "votes": votes,
            "plot": movie_info.get("overview", "N/A"),
            "poster_url": (
                f"{TMDB_IMAGE_BASE_URL}w500{movie_info.get('poster_path')}"
                if movie_info.get("poster_path")
                else None
            ),
            "year": (
                movie_info.get("release_date", "N/A")[:4]
                if movie_info.get("release_date")
                else "N/A"
            ),
            "cast": tmdb_cast_info,
            "images": tmdb_image_info,
            "trailer": tmdb_movie_trailer,
            "credits": tmdb_credits,
            "backdrop_url": backdrop_url,
        }

        method_time = time.time() - start_time
        logger.info(
            f"Completed get_movie_data for {self.tconst} in {method_time:.2f} seconds."
        )

        return self.movie_data

    async def close(self):
        await self.client.aclose()  # Close the client session when done


async def main():

    tconst = "tt0182727"  # Example IMDb ID
    db_config = Config.get_db_config()  # Your database configuration
    pool = DatabaseConnectionPool(db_config)
    await pool.init_pool()
    movie_instance = Movie(tconst, pool)
    movie_data = await movie_instance.get_movie_data()
    if movie_data:
        print(f"Movie Data: {movie_data}")
    else:
        print("Failed to fetch movie data.")
    await pool.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
