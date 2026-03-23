import asyncio
import logging as _logging
import time

from database.errors import DatabaseError
from scripts.tmdb_client import TMDbHelper
from logging_config import get_logger

logger = get_logger(__name__)
# Set httpx logging level to ERROR to reduce verbosity
_logging.getLogger("httpx").setLevel(_logging.ERROR)


def build_ratings_query():
    return """
    SELECT tr.tconst, tr.averageRating, tr.numVotes
    FROM `title.ratings` tr
    WHERE tr.tconst = %s
    """


class Movie:
    def __init__(self, tconst, db_pool, tmdb_helper=None):
        self.tconst = tconst
        self.db_pool = db_pool
        self.movie_data = {}
        # Re-use a shared TMDbHelper (and its httpx connection pool) when
        # provided; fall back to creating one for backward compatibility.
        self.tmdb_helper = tmdb_helper or TMDbHelper()
        self._owns_tmdb_helper = tmdb_helper is None
        self.slug = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    async def fetch_movie_slug(self):
        """Fetch slug using a single UNION ALL query across all tables."""
        try:
            query = (
                "SELECT slug FROM popular_movies_cache WHERE tconst = %s "
                "UNION ALL "
                "SELECT slug FROM recent_movies_cache WHERE tconst = %s "
                "UNION ALL "
                "SELECT slug FROM `title.basics` WHERE tconst = %s "
                "LIMIT 1"
            )
            result = await self.db_pool.execute(
                query, [self.tconst, self.tconst, self.tconst], fetch="one"
            )
            self.slug = result["slug"] if result and result.get("slug") else None
        except Exception as e:
            logger.debug("Error fetching slug for %s: %s", self.tconst, e)
            self.slug = None

    async def fetch_movie_ratings(self, tconst):
        start_time = time.time()  # Start timing

        query = build_ratings_query()
        try:
            result = await self.db_pool.execute(query, [tconst], fetch="one")
        except DatabaseError as e:
            logger.warning("Database error fetching ratings for %s: %s", tconst, e)
            return None

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

                logger.info("Ratings data: %s", ratings_data)  # Log the ratings data

                query_time = time.time() - start_time  # Measure query execution time
                logger.info("Fetched movie ratings in %.2f seconds", query_time)

                return ratings_data

            except KeyError as e:
                logger.error("Error in fetch_movie_ratings: %s", e)
                logger.error("Result missing expected key: %s", result)
                return None
        else:
            logger.info("No ratings found for tconst: %s", tconst)
            return None

    async def get_movie_data(self):
        start_time = time.time()

        try:
            # Parallel fetch of basic data and TMDB ID
            basic_tasks = [
                self.tmdb_helper.get_tmdb_id_by_tconst(self.tconst),
                self.fetch_movie_slug()
            ]
            basic_results = await asyncio.gather(*basic_tasks, return_exceptions=True)
            
            tmdb_id = basic_results[0] if not isinstance(basic_results[0], Exception) else None
            
            if not tmdb_id:
                logger.warning("No TMDB ID found for tconst: %s", self.tconst)
                return None

            # Execute all data fetching concurrently — credits are fetched
            # once and cast info is derived from the same response (saves one
            # TMDb API call per movie).
            tasks = [
                self.tmdb_helper.get_movie_info_by_tmdb_id(tmdb_id),
                self.tmdb_helper.get_credits_by_tmdb_id(tmdb_id),
                self.tmdb_helper.get_video_url_by_tmdb_id(tmdb_id),
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
            tmdb_image_info = results[3] if not isinstance(results[3], Exception) else {}
            age_rating = results[4] if not isinstance(results[4], Exception) else "Not Rated"
            ratings_data = results[5] if not isinstance(results[5], Exception) else None
            watch_providers = results[6] if not isinstance(results[6], Exception) else None

            # Derive cast info from the single credits response
            tmdb_cast_info_result = [
                {
                    "name": m["name"],
                    "image_url": (
                        f"{self.tmdb_helper.image_base_url}w185{m['profile_path']}"
                        if m.get("profile_path") else None
                    ),
                    "character": m.get("character", "N/A"),
                }
                for m in tmdb_credits.get("cast", [])[:10]
            ]
            
            # Log any errors that occurred
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning("Task %s failed for %s: %s", i, self.tconst, result)

            tmdb_cast_info = tmdb_cast_info_result

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
                    f"{self.tmdb_helper.image_base_url}w500{movie_info.get('poster_path')}"
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
                "_full": True,  # sentinel for _is_full_movie()
            }

            method_time = time.time() - start_time
            logger.info(
                "Completed get_movie_data for %s in %.2f seconds (parallel)", self.tconst, method_time
            )

            return self.movie_data
            
        except Exception as e:
            logger.error("Error fetching movie data for %s: %s", self.tconst, e)
            return None

    async def close(self):
        """Close underlying HTTP clients (only if this instance owns them)."""
        if self._owns_tmdb_helper:
            await self.tmdb_helper.close()
