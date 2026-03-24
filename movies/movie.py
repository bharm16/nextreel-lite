import asyncio
import logging as _logging
import time

from infra.errors import DatabaseError
from movies.tmdb_client import TMDbHelper
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
        """Fetch slug from the source-of-truth table (title.basics).

        Cache tables replicate slugs from title.basics, so querying the
        canonical table directly is sufficient and avoids a 3-table UNION ALL.
        """
        try:
            query = "SELECT slug FROM `title.basics` WHERE tconst = %s"
            result = await self.db_pool.execute(
                query, [self.tconst], fetch="one"
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
            # Phase 1: resolve TMDb ID + fetch slug in parallel
            basic_tasks = [
                self.tmdb_helper.get_tmdb_id_by_tconst(self.tconst),
                self.fetch_movie_slug(),
            ]
            basic_results = await asyncio.gather(*basic_tasks, return_exceptions=True)

            tmdb_id = basic_results[0] if not isinstance(basic_results[0], Exception) else None

            if not tmdb_id:
                logger.warning("No TMDB ID found for tconst: %s", self.tconst)
                return None

            # Phase 2: single combined TMDb call + local DB ratings in parallel
            tmdb_task = self.tmdb_helper.get_movie_full(tmdb_id)
            ratings_task = self.fetch_movie_ratings(self.tconst)
            results = await asyncio.gather(tmdb_task, ratings_task, return_exceptions=True)

            full_data = results[0] if not isinstance(results[0], Exception) else {}
            ratings_data = results[1] if not isinstance(results[1], Exception) else None

            if isinstance(results[0], Exception):
                logger.warning("TMDb combined fetch failed for %s: %s", self.tconst, results[0])
            if isinstance(results[1], Exception):
                logger.warning("Ratings fetch failed for %s: %s", self.tconst, results[1])

            # Phase 3: parse all fields from the combined response
            h = self.tmdb_helper
            tmdb_cast_info = h.parse_cast(full_data)
            directors = h.parse_directors(full_data)
            key_crew = h.parse_key_crew(full_data)
            trailer = h.parse_trailer(full_data)
            images = h.parse_images(full_data)
            age_rating = h.parse_age_rating(full_data)
            watch_providers = h.parse_watch_providers(full_data)
            keywords = h.parse_keywords(full_data)
            recommendations = h.parse_recommendations(full_data)
            external_ids = h.parse_external_ids(full_data)
            collection = h.parse_collection(full_data)

            backdrop_url = images["backdrops"][0] if images.get("backdrops") else None

            # Use database rating if available; otherwise, fall back to TMDB rating
            rating = (
                ratings_data["averageRating"]
                if ratings_data and ratings_data["averageRating"] != "N/A"
                else full_data.get("vote_average", "N/A")
            )
            votes = (
                ratings_data["numVotes"]
                if ratings_data and ratings_data["numVotes"] != "N/A"
                else full_data.get("vote_count", "N/A")
            )

            # Format budget and revenue
            budget = full_data.get("budget", 0)
            revenue = full_data.get("revenue", 0)
            budget_formatted = f"${budget:,}" if budget > 0 else "Unknown"
            revenue_formatted = f"${revenue:,}" if revenue > 0 else "Unknown"

            # Get production countries
            countries = full_data.get("production_countries", [])
            country_names = [country.get("name", "") for country in countries[:3]]

            self.movie_data = {
                "title": full_data.get("title", "N/A"),
                "imdb_id": self.tconst,
                "tmdb_id": tmdb_id,
                "slug": self.slug,
                "genres": ", ".join(
                    [genre["name"] for genre in full_data.get("genres", [])]
                ),
                "directors": ", ".join(directors),
                "rating": rating,
                "votes": votes,
                "plot": full_data.get("overview", "N/A"),
                "poster_url": (
                    f"{h.image_base_url}w500{full_data.get('poster_path')}"
                    if full_data.get("poster_path")
                    else None
                ),
                "year": (
                    full_data.get("release_date", "N/A")[:4]
                    if full_data.get("release_date")
                    else "N/A"
                ),
                "cast": tmdb_cast_info,
                "images": images,
                "trailer": trailer,
                "credits": full_data.get("credits", {}),
                "backdrop_url": backdrop_url,
                "original_language": full_data.get("original_language", "unknown"),
                "spoken_languages": [
                    lang.get("iso_639_1") for lang in full_data.get("spoken_languages", [])
                ],
                "age_rating": age_rating,
                "budget": budget_formatted,
                "revenue": revenue_formatted,
                "runtime": (
                    f"{full_data.get('runtime', 0)} min"
                    if full_data.get("runtime")
                    else "Unknown"
                ),
                "production_countries": ", ".join(country_names) if country_names else "Unknown",
                "status": full_data.get("status", "Unknown"),
                "tagline": full_data.get("tagline", ""),
                "watch_providers": watch_providers,
                # New enriched fields
                "key_crew": key_crew,
                "keywords": keywords,
                "recommendations": recommendations,
                "external_ids": external_ids,
                "collection": collection,
                "homepage": full_data.get("homepage", ""),
                "_full": True,  # sentinel for _is_full_movie()
            }

            method_time = time.time() - start_time
            logger.info(
                "Completed get_movie_data for %s in %.2f seconds", self.tconst, method_time
            )

            return self.movie_data

        except Exception as e:
            logger.error("Error fetching movie data for %s: %s", self.tconst, e)
            return None

    async def close(self):
        """Close underlying HTTP clients (only if this instance owns them)."""
        if self._owns_tmdb_helper:
            await self.tmdb_helper.close()
