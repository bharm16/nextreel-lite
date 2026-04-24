from __future__ import annotations

import asyncio
import logging as _logging
import time
from typing import Any

from infra.errors import DatabaseError
from infra.pool import DatabaseConnectionPool
from movies.movie_payload import MoviePayloadFormatter
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
        self.payload_formatter = MoviePayloadFormatter()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    async def fetch_slug_and_ratings(self):
        """Fetch slug and ratings for ``self.tconst``.

        Prefers the denormalized ``movie_candidates`` row (which already
        carries slug + averageRating + numVotes) to avoid a redundant JOIN
        against ``title.basics`` + ``title.ratings``. Falls back to the
        JOIN only when the tconst is absent from ``movie_candidates``
        (e.g. newly-imported titles not yet swept into the candidate
        cache).
        """
        start_time = time.time()
        try:
            candidate_row = await self.db_pool.execute(
                """
                SELECT slug, averageRating, numVotes
                FROM movie_candidates
                WHERE tconst = %s
                """,
                [self.tconst],
                fetch="one",
            )
        except DatabaseError as e:
            logger.warning(
                "Database error fetching candidate ratings for %s: %s", self.tconst, e
            )
            candidate_row = None

        if candidate_row:
            self.slug = candidate_row.get("slug")
            ratings_data = {
                "tconst": self.tconst,
                "averageRating": (
                    candidate_row["averageRating"]
                    if candidate_row.get("averageRating") is not None
                    else "N/A"
                ),
                "numVotes": (
                    candidate_row["numVotes"]
                    if candidate_row.get("numVotes") is not None
                    else "N/A"
                ),
            }
            query_time = time.time() - start_time
            logger.info(
                "Fetched slug+ratings for %s from movie_candidates in %.2fs",
                self.tconst,
                query_time,
            )
            return ratings_data

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
        logger.info(
            "Fetched slug+ratings for %s from title.basics JOIN in %.2fs",
            self.tconst,
            query_time,
        )
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

            self.movie_data = self.payload_formatter.assemble(
                full_data=full_data,
                ratings_data=ratings_data,
                tmdb_helper=self.tmdb_helper,
                tconst=self.tconst,
                slug=self.slug,
                tmdb_id=tmdb_id,
            )

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
