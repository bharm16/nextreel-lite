"""Projection-table rendering source and async enrichment hooks."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from infra.time_utils import utcnow
from logging_config import get_logger
from movies.movie import Movie
from movies.projection_state import (
    ENQUEUE_COOLDOWN,
    STALE_AFTER,
    ProjectionState,
)

logger = get_logger(__name__)

PLACEHOLDER_POSTER = "/static/img/poster-placeholder.svg"
PLACEHOLDER_BACKDROP = "/static/img/backdrop-placeholder.svg"

# Backward-compatible string constants (used by tests and callers).
PROJECTION_READY = ProjectionState.READY.value
PROJECTION_STALE = ProjectionState.STALE.value
PROJECTION_CORE = ProjectionState.CORE.value
PROJECTION_FAILED = ProjectionState.FAILED.value


class ProjectionStore:
    def __init__(self, db_pool, tmdb_helper=None, enqueue_fn=None):
        self.db_pool = db_pool
        self.tmdb_helper = tmdb_helper
        self.enqueue_fn = enqueue_fn
        self._local_enrichment_tconsts: set[str] = set()
        self._local_enrichment_tasks: set[asyncio.Task] = set()

    def _payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload_json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("projection_state", row.get("projection_state"))
        return payload

    async def _select_row(self, tconst: str) -> dict[str, Any] | None:
        return await self.db_pool.execute(
            """
            SELECT tconst, tmdb_id, payload_json, projection_state,
                   enriched_at, stale_after, last_attempt_at, attempt_count, last_error
            FROM movie_projection
            WHERE tconst = %s
            """,
            [tconst],
            fetch="one",
        )

    async def fetch_renderable_payload(self, tconst: str) -> dict[str, Any] | None:
        row = await self._select_row(tconst)
        now = utcnow()
        if row:
            state = row["projection_state"]
            stale_after = row.get("stale_after")
            if state == PROJECTION_READY and stale_after and stale_after <= now:
                await self.db_pool.execute(
                    """
                    UPDATE movie_projection
                    SET projection_state = %s
                    WHERE tconst = %s AND projection_state = %s
                    """,
                    [PROJECTION_STALE, tconst, PROJECTION_READY],
                    fetch="none",
                )
                row["projection_state"] = PROJECTION_STALE
                state = PROJECTION_STALE

            if state == PROJECTION_READY:
                return self._payload_from_row(row)
            if state == PROJECTION_STALE:
                await self._enqueue_enrichment_if_needed(
                    tconst,
                    row,
                    tmdb_id=row.get("tmdb_id"),
                )
                return self._payload_from_row(row)
            if ProjectionState(state).needs_enrichment():
                payload = self._payload_from_row(row)
                if not payload or payload.get("projection_state") == PROJECTION_FAILED:
                    payload = await self.ensure_core_projection(tconst)
                if payload:
                    await self._enqueue_enrichment_if_needed(
                        tconst,
                        row,
                        tmdb_id=row.get("tmdb_id"),
                    )
                return payload

        # Ensure a core projection exists first.
        payload = await self.ensure_core_projection(tconst)
        if not payload:
            return None

        await self._enqueue_enrichment_if_needed(tconst, row)
        return payload

    @staticmethod
    def _persisted_payload(payload: dict[str, Any]) -> dict[str, Any]:
        slimmed = dict(payload)
        slimmed.pop("images", None)
        slimmed.pop("credits", None)
        return slimmed

    async def _mark_attempt(self, tconst: str, now: datetime) -> None:
        await self.db_pool.execute(
            """
            UPDATE movie_projection
            SET last_attempt_at = %s
            WHERE tconst = %s
            """,
            [now, tconst],
            fetch="none",
        )

    async def _schedule_local_enrichment(self, tconst: str, tmdb_id: int | None = None) -> bool:
        if not self.tmdb_helper or tconst in self._local_enrichment_tconsts:
            return False

        self._local_enrichment_tconsts.add(tconst)

        async def _run() -> None:
            try:
                await self.enrich_projection(tconst, known_tmdb_id=tmdb_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Local enrichment failed for %s: %s", tconst, exc)
            finally:
                self._local_enrichment_tconsts.discard(tconst)

        task = asyncio.create_task(_run())
        self._local_enrichment_tasks.add(task)
        task.add_done_callback(self._local_enrichment_tasks.discard)
        return True

    async def ensure_core_projection(self, tconst: str) -> dict[str, Any] | None:
        query = """
        SELECT
            tb.tconst,
            tb.primaryTitle,
            tb.startYear,
            tb.genres,
            tb.language,
            tb.slug,
            COALESCE(tr.averageRating, 0) AS averageRating,
            COALESCE(tr.numVotes, 0) AS numVotes
        FROM `title.basics` tb
        LEFT JOIN `title.ratings` tr ON tb.tconst = tr.tconst
        WHERE tb.tconst = %s
        """
        row = await self.db_pool.execute(query, [tconst], fetch="one")
        if not row:
            return None

        payload = self.build_core_payload(row)
        now = utcnow()
        await self.db_pool.execute(
            """
            INSERT INTO movie_projection (
                tconst, tmdb_id, payload_json, projection_state,
                enriched_at, stale_after, last_attempt_at, attempt_count, last_error
            )
            VALUES (%s, %s, %s, %s, NULL, NULL, NULL, 0, NULL)
            AS new_row
            ON DUPLICATE KEY UPDATE
                payload_json = CASE
                    WHEN movie_projection.projection_state IN ('ready', 'stale') THEN movie_projection.payload_json
                    ELSE new_row.payload_json
                END,
                projection_state = CASE
                    WHEN movie_projection.projection_state IN ('ready', 'stale') THEN movie_projection.projection_state
                    ELSE new_row.projection_state
                END,
                last_attempt_at = COALESCE(movie_projection.last_attempt_at, %s)
            """,
            [
                tconst,
                None,
                json.dumps(self._persisted_payload(payload)),
                PROJECTION_CORE,
                now,
            ],
            fetch="none",
        )
        return payload

    def build_core_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        language = row.get("language") or "unknown"
        genres = row.get("genres") or "Unknown"
        rating = row.get("averageRating") or 0
        votes = row.get("numVotes") or 0
        return {
            "title": row.get("primaryTitle") or "Unknown",
            "imdb_id": row["tconst"],
            "tmdb_id": None,
            "slug": row.get("slug"),
            "genres": genres,
            "directors": "Unknown",
            "rating": float(rating),
            "votes": int(votes),
            "plot": "Additional details are still loading for this title.",
            "poster_url": PLACEHOLDER_POSTER,
            "year": str(row.get("startYear") or "Unknown"),
            "cast": [],
            "trailer": None,
            "backdrop_url": PLACEHOLDER_BACKDROP,
            "original_language": language,
            "spoken_languages": [language] if language != "unknown" else [],
            "age_rating": "Not Rated",
            "budget": "Unknown",
            "revenue": "Unknown",
            "runtime": "Unknown",
            "production_countries": "Unknown",
            "status": "Unknown",
            "tagline": "",
            "watch_providers": None,
            "key_crew": [],
            "keywords": [],
            "recommendations": [],
            "external_ids": {},
            "collection": None,
            "homepage": "",
            "_full": False,
            "projection_state": PROJECTION_CORE,
        }

    async def _enqueue_enrichment_if_needed(
        self,
        tconst: str,
        row: dict[str, Any] | None,
        tmdb_id: int | None = None,
    ) -> bool:
        """Try to enqueue background enrichment. Returns True if enqueued."""
        now = utcnow()
        last_attempt_at = row.get("last_attempt_at") if row else None
        if last_attempt_at and now < last_attempt_at + ENQUEUE_COOLDOWN:
            return False

        enqueue = self.enqueue_fn
        if enqueue:
            try:
                result = await enqueue("enrich_projection", tconst, tmdb_id)
                if result is not None:
                    await self._mark_attempt(tconst, now)
                    return True
            except Exception as exc:
                logger.debug("Failed to enqueue enrich_projection(%s): %s", tconst, exc)

        scheduled = await self._schedule_local_enrichment(tconst, tmdb_id=tmdb_id)
        if scheduled:
            await self._mark_attempt(tconst, now)
        return scheduled

    async def ready_check(self) -> bool:
        await self.db_pool.execute(
            """
            SELECT 1 AS ready
            FROM movie_projection
            LIMIT 1
            """,
            fetch="one",
        )
        return True

    async def _upsert_ready(
        self, tconst: str, payload: dict[str, Any], now: datetime, attempts: int,
    ) -> None:
        """Persist a successfully enriched projection."""
        await self.db_pool.execute(
            """
            INSERT INTO movie_projection (
                tconst, tmdb_id, payload_json, projection_state,
                enriched_at, stale_after, last_attempt_at, attempt_count, last_error
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
            AS new_row
            ON DUPLICATE KEY UPDATE
                tmdb_id = new_row.tmdb_id,
                payload_json = new_row.payload_json,
                projection_state = new_row.projection_state,
                enriched_at = new_row.enriched_at,
                stale_after = new_row.stale_after,
                last_attempt_at = new_row.last_attempt_at,
                attempt_count = new_row.attempt_count,
                last_error = NULL
            """,
            [
                tconst,
                payload.get("tmdb_id"),
                json.dumps(self._persisted_payload(payload)),
                PROJECTION_READY,
                now,
                now + STALE_AFTER,
                now,
                attempts,
            ],
            fetch="none",
        )

    async def _upsert_failed(
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
        error: str,
        tmdb_id: int | None = None,
    ) -> None:
        """Record a failed enrichment attempt."""
        await self.db_pool.execute(
            """
            INSERT INTO movie_projection (
                tconst, tmdb_id, payload_json, projection_state,
                enriched_at, stale_after, last_attempt_at, attempt_count, last_error
            )
            VALUES (%s, %s, %s, %s, NULL, NULL, %s, %s, %s)
            AS new_row
            ON DUPLICATE KEY UPDATE
                tmdb_id = COALESCE(movie_projection.tmdb_id, new_row.tmdb_id),
                projection_state = new_row.projection_state,
                last_attempt_at = new_row.last_attempt_at,
                attempt_count = new_row.attempt_count,
                last_error = new_row.last_error,
                payload_json = COALESCE(payload_json, new_row.payload_json)
            """,
            [
                tconst,
                tmdb_id,
                json.dumps(self._persisted_payload(payload)),
                PROJECTION_FAILED,
                now,
                attempts,
                error,
            ],
            fetch="none",
        )

    async def enrich_projection(self, tconst: str, known_tmdb_id: int | None = None) -> dict[str, Any] | None:
        now = utcnow()
        row = await self._select_row(tconst)
        attempts = int(row.get("attempt_count", 0)) + 1 if row else 1
        tmdb_id = known_tmdb_id if known_tmdb_id is not None else (row or {}).get("tmdb_id")
        try:
            movie = Movie(tconst, self.db_pool, tmdb_helper=self.tmdb_helper)
            payload = await movie.get_movie_data(known_tmdb_id=tmdb_id)
            if not payload:
                raise RuntimeError("TMDB enrichment returned no payload")

            payload["tmdb_id"] = payload.get("tmdb_id") or tmdb_id
            payload["projection_state"] = PROJECTION_READY
            await self._upsert_ready(tconst, payload, now, attempts)
            return payload
        except Exception as exc:
            core_payload = await self.ensure_core_projection(tconst)
            await self._upsert_failed(
                tconst,
                core_payload or {},
                now,
                attempts,
                str(exc),
                tmdb_id=tmdb_id,
            )
            logger.warning("Projection enrichment failed for %s: %s", tconst, exc)
            return core_payload

    async def requeue_stale_projections(self) -> int:
        now = utcnow()
        result = await self.db_pool.execute(
            """
            UPDATE movie_projection
            SET projection_state = %s
            WHERE projection_state = %s
              AND stale_after IS NOT NULL
              AND stale_after <= %s
            """,
            [PROJECTION_STALE, PROJECTION_READY, now],
            fetch="none",
        )
        return result if isinstance(result, int) else 0
