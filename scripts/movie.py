import asyncio
import logging
from logging_config import get_logger
import os
import time

import httpx

from settings import Config, DatabaseConnectionPool
from db_utils import DatabaseQueryExecutor
from scripts.filter_backend import ImdbRandomMovieFetcher
from scripts.tmdb_client import TMDbHelper, get_tmdb_api_key

# Configure logging for better debugging
logger = get_logger(__name__)
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


TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"


# Async HTTP client setup


# Async TMDb operations
async def get_tmdb_id_by_tconst(tconst, client):
    api_key = get_tmdb_api_key()
    response = await client.get(
        f"{TMDB_API_BASE_URL}/find/{tconst}",
        params={"api_key": api_key, "external_source": "imdb_id"},
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
    def __init__(self, tconst, db_pool):
        self.tconst = tconst
        self.db_pool = db_pool
        self.movie_data = {}
        self.query_executor = DatabaseQueryExecutor(db_pool)
        self.tmdb_helper = TMDbHelper()  # Initialize TMDbHelper using env key
        self.slug = None  # Assuming slug is available at initialization
        self.client = httpx.AsyncClient()  # Initialize once and reuse

    async def fetch_movie_slug(self):
        start_time = time.time()  # Start timing

        query = """
           SELECT slug FROM `title.basics` WHERE tconst = %s;
           """
        result = await self.query_executor.execute_async_query(
            query, [self.tconst], fetch="one"
        )
        if result:
            self.slug = result["slug"]  # Assuming the column name in the DB is 'slug'
            logger.info(f"Slug for tconst {self.tconst}: {self.slug}")
        else:
            logger.warning(f"No slug found for tconst: {self.tconst}")

        method_time = time.time() - start_time
        logger.info(
            f"Completed fetch_movie_slug for {self.tconst} in {method_time:.2f} seconds."
        )

    # Assume the necessary imports and setup for logging are done elsewhere in your code

    async def fetch_movie_ratings(self, tconst):
        start_time = time.time()  # Start timing

        query = build_ratings_query()
        result = await self.query_executor.execute_async_query(
            query, [tconst], fetch="one"
        )

        if result:
            try:
                # Accessing result as a dictionary
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

                logger.info(f"Ratings data: {ratings_data}")  # Log the ratings data

                query_time = time.time() - start_time  # Measure query execution time
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
        start_time = time.time()

        try:
            # Check cache first (assuming redis_client exists)
            cache_key = f"movie:full:{self.tconst}"
            if hasattr(self, 'redis_client') and self.redis_client:
                try:
                    import json
                    cached = await self.redis_client.get(cache_key)
                    if cached:
                        logger.debug(f"Cache hit for movie {self.tconst}")
                        return json.loads(cached)
                except Exception as e:
                    logger.warning(f"Cache read failed: {e}")

            # Parallel fetch of basic data and TMDB ID
            basic_tasks = [
                self.tmdb_helper.get_tmdb_id_by_tconst(self.tconst),
                self.fetch_movie_slug()
            ]
            basic_results = await asyncio.gather(*basic_tasks, return_exceptions=True)
            
            tmdb_id = basic_results[0] if not isinstance(basic_results[0], Exception) else None
            
            if not tmdb_id:
                logger.warning(f"No TMDB ID found for tconst: {self.tconst}")
                return None

            # Execute all data fetching coroutines concurrently with error handling
            tasks = [
                self.tmdb_helper.get_movie_info_by_tmdb_id(tmdb_id),
                self.tmdb_helper.get_credits_by_tmdb_id(tmdb_id),
                self.tmdb_helper.get_video_url_by_tmdb_id(tmdb_id),
                self.tmdb_helper.get_cast_info_by_tmdb_id(tmdb_id),
                self.tmdb_helper.get_images_by_tmdb_id(tmdb_id),
                self.tmdb_helper.get_age_rating_by_tmdb_id(tmdb_id),
                self.fetch_movie_ratings(self.tconst),
                self.tmdb_helper.get_watch_providers_by_tmdb_id(tmdb_id),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results with error handling
            movie_info = results[0] if not isinstance(results[0], Exception) else {}
            tmdb_credits = results[1] if not isinstance(results[1], Exception) else {}
            tmdb_movie_trailer = results[2] if not isinstance(results[2], Exception) else None
            tmdb_cast_info_result = results[3] if not isinstance(results[3], Exception) else []
            tmdb_image_info = results[4] if not isinstance(results[4], Exception) else {}
            age_rating = results[5] if not isinstance(results[5], Exception) else "Not Rated"
            ratings_data = results[6] if not isinstance(results[6], Exception) else None
            watch_providers = results[7] if not isinstance(results[7], Exception) else None
            
            # Log any errors that occurred
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"Task {i} failed for {self.tconst}: {result}")

            tmdb_cast_info = (
                tmdb_cast_info_result[:10] if tmdb_cast_info_result else []
            )  # Limit to 10 cast members

            backdrop_url = (
                tmdb_image_info["backdrops"][0]
                if tmdb_image_info.get("backdrops")
                else None
            )

            # Use database rating if available; otherwise, fall back to TMDB rating
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

            # Format budget and revenue
            budget = movie_info.get("budget", 0)
            revenue = movie_info.get("revenue", 0)
            budget_formatted = f"${budget:,}" if budget > 0 else "Unknown"
            revenue_formatted = f"${revenue:,}" if revenue > 0 else "Unknown"
            
            # Get production countries
            countries = movie_info.get("production_countries", [])
            country_names = [country.get("name", "") for country in countries[:3]]  # Limit to 3
            
            self.movie_data = {
                "title": movie_info.get("title", "N/A"),
                "imdb_id": self.tconst,
                "tmdb_id": tmdb_id,
                "slug": self.slug,
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
                "original_language": movie_info.get("original_language", "unknown"),
                "spoken_languages": [lang.get("iso_639_1") for lang in movie_info.get("spoken_languages", [])],
                # New TMDB data
                "age_rating": age_rating,
                "budget": budget_formatted,
                "revenue": revenue_formatted,
                "runtime": f"{movie_info.get('runtime', 0)} min" if movie_info.get('runtime') else "Unknown",
                "production_countries": ", ".join(country_names) if country_names else "Unknown",
                "status": movie_info.get("status", "Unknown"),
                "tagline": movie_info.get("tagline", ""),
                "watch_providers": watch_providers,
            }

            # Cache the complete result
            if hasattr(self, 'redis_client') and self.redis_client and self.movie_data:
                try:
                    import json
                    await self.redis_client.setex(
                        cache_key,
                        3600,  # 1 hour TTL
                        json.dumps(self.movie_data)
                    )
                except Exception as e:
                    logger.warning(f"Cache write failed: {e}")

            method_time = time.time() - start_time
            logger.info(
                f"Completed get_movie_data for {self.tconst} in {method_time:.2f} seconds (parallel)"
            )

            return self.movie_data
            
        except Exception as e:
            logger.error(f"Error fetching movie data for {self.tconst}: {e}")
            return None

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
