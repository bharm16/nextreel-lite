from __future__ import annotations

import asyncio
import logging as _logging
import time
from typing import Any

from infra.errors import DatabaseError
from infra.pool import DatabaseConnectionPool
from movies.tmdb_client import TMDbHelper
from logging_config import get_logger

logger = get_logger(__name__)
_logging.getLogger("httpx").setLevel(_logging.ERROR)


class Movie:
    def __init__(
        self,
        tconst: str,
        db_pool: DatabaseConnectionPool,
        tmdb_helper: TMDbHelper | None = None,
    ) -> None:
        self.tconst = tconst
        self.db_pool = db_pool
        self.movie_data: dict[str, Any] = {}
        self.tmdb_helper = tmdb_helper or TMDbHelper()
        self._owns_tmdb_helper = tmdb_helper is None
        self.slug: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    async def fetch_slug_and_ratings(self):
        """Fetch slug and ratings in a single query via JOIN."""
        start_time = time.time()
        try:
            result = await self.db_pool.execute(
                """
                SELECT tb.slug, tr.tconst, tr.averageRating, tr.numVotes
                FROM `title.basics` tb
                LEFT JOIN `title.ratings` tr ON tb.tconst = tr.tconst
                WHERE tb.tconst = %s
                """,
                [self.tconst],
                fetch="one",
            )
        except DatabaseError as e:
            logger.warning("Database error fetching slug+ratings for %s: %s", self.tconst, e)
            return None

        if not result:
            logger.info("No data found for tconst: %s", self.tconst)
            return None

        self.slug = result.get("slug")

        ratings_data = {
            "tconst": result.get("tconst") or self.tconst,
            "averageRating": (
                result["averageRating"] if result.get("averageRating") is not None else "N/A"
            ),
            "numVotes": (result["numVotes"] if result.get("numVotes") is not None else "N/A"),
        }

        query_time = time.time() - start_time
        logger.info("Fetched slug+ratings for %s in %.2f seconds", self.tconst, query_time)
        return ratings_data

    async def get_movie_data(self, known_tmdb_id: int | None = None) -> dict[str, Any] | None:
        start_time = time.time()

        ratings_task: asyncio.Task | None = None
        full_task: asyncio.Task | None = None
        try:
            ratings_task = asyncio.create_task(self.fetch_slug_and_ratings())
            tmdb_id = known_tmdb_id
            if tmdb_id is None:
                try:
                    tmdb_id = await self.tmdb_helper.get_tmdb_id_by_tconst(self.tconst)
                except Exception as exc:
                    logger.warning("TMDb ID lookup failed for %s: %s", self.tconst, exc)
                    tmdb_id = None

            if not tmdb_id:
                # Await the ratings task for clean shutdown before returning.
                try:
                    await ratings_task
                except Exception:  # pragma: no cover - defensive
                    pass
                ratings_task = None
                logger.warning("No TMDB ID found for tconst: %s", self.tconst)
                return None

            # Start the full-data fetch immediately so it runs concurrently
            # with whatever remains of the ratings query. The existing TMDb
            # semaphore and circuit breaker in TMDbHelper still bound fan-out.
            full_task = asyncio.create_task(self.tmdb_helper.get_movie_full(tmdb_id))

            try:
                ratings_data = await ratings_task
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Ratings fetch failed for %s: %s", self.tconst, exc)
                ratings_data = None
            finally:
                ratings_task = None

            try:
                full_data = await full_task
            except Exception as exc:
                logger.warning("TMDb combined fetch failed for %s: %s", self.tconst, exc)
                full_data = {}
            finally:
                full_task = None
            if full_data is None:
                full_data = {}

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

            budget = full_data.get("budget", 0)
            revenue = full_data.get("revenue", 0)
            budget_formatted = f"${budget:,}" if budget > 0 else "Unknown"
            revenue_formatted = f"${revenue:,}" if revenue > 0 else "Unknown"

            countries = full_data.get("production_countries", [])
            country_names = [country.get("name", "") for country in countries[:3]]

            self.movie_data = {
                "title": full_data.get("title", "N/A"),
                "imdb_id": self.tconst,
                "tmdb_id": tmdb_id,
                "slug": self.slug,
                "genres": ", ".join([genre["name"] for genre in full_data.get("genres", [])]),
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
                "trailer": trailer,
                "backdrop_url": backdrop_url,
                "original_language": full_data.get("original_language", "unknown"),
                "spoken_languages": [
                    lang.get("iso_639_1") for lang in full_data.get("spoken_languages", [])
                ],
                "age_rating": age_rating,
                "budget": budget_formatted,
                "revenue": revenue_formatted,
                "runtime": (
                    f"{full_data.get('runtime', 0)} min" if full_data.get("runtime") else "Unknown"
                ),
                "production_countries": ", ".join(country_names) if country_names else "Unknown",
                "status": full_data.get("status", "Unknown"),
                "tagline": full_data.get("tagline", ""),
                "watch_providers": watch_providers,
                "key_crew": key_crew,
                "keywords": keywords,
                "recommendations": recommendations,
                "external_ids": external_ids,
                "collection": collection,
                "homepage": full_data.get("homepage", ""),
                "_full": True,  # sentinel for _is_full_movie()
            }

            method_time = time.time() - start_time
            logger.info("Completed get_movie_data for %s in %.2f seconds", self.tconst, method_time)

            return self.movie_data

        except Exception as e:
            logger.error("Error fetching movie data for %s: %s", self.tconst, e, exc_info=True)
            return None
        finally:
            # Guarantee every spawned task is awaited or cancelled on every
            # exit path so we never leak a dangling coroutine.
            for task in (ratings_task, full_task):
                if task is None:
                    continue
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        """Close underlying HTTP clients (only if this instance owns them)."""
        if self._owns_tmdb_helper:
            await self.tmdb_helper.close()
