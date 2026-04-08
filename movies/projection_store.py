"""Projection-table rendering source and async enrichment hooks."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from infra.time_utils import utcnow
from movies.projection_enrichment import ProjectionEnrichmentCoordinator
from movies.projection_state import (
    STALE_AFTER,
    ProjectionState,
)

if TYPE_CHECKING:
    import asyncio

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
        self.coordinator = ProjectionEnrichmentCoordinator(
            self,
            tmdb_helper=tmdb_helper,
            enqueue_fn=enqueue_fn,
        )

    def attach_coordinator(
        self,
        coordinator: ProjectionEnrichmentCoordinator,
    ) -> ProjectionEnrichmentCoordinator:
        coordinator.store = self
        self.coordinator = coordinator
        return coordinator

    @property
    def tmdb_helper(self):
        return self.coordinator.tmdb_helper

    @tmdb_helper.setter
    def tmdb_helper(self, value) -> None:
        self.coordinator.tmdb_helper = value

    @property
    def enqueue_fn(self):
        return self.coordinator.enqueue_fn

    @enqueue_fn.setter
    def enqueue_fn(self, value) -> None:
        self.coordinator.enqueue_fn = value

    @property
    def _local_enrichment_tasks(self) -> "set[asyncio.Task]":
        return self.coordinator._local_enrichment_tasks

    def _payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload_json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("projection_state", row.get("projection_state"))
        payload.setdefault("tconst", row.get("tconst"))
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
                await self._maybe_enqueue_enrichment(
                    tconst,
                    row,
                    tmdb_id=row.get("tmdb_id"),
                )
                return self._payload_from_row(row)
            if ProjectionState(state).needs_enrichment():
                enriched = await self.enrich_projection(
                    tconst,
                    known_tmdb_id=row.get("tmdb_id"),
                )
                if enriched:
                    return enriched
                payload = self._payload_from_row(row)
                if not payload or payload.get("projection_state") == PROJECTION_FAILED:
                    payload = await self.ensure_core_projection(tconst)
                return payload

        # No projection row — enrich inline so the first render has full data.
        enriched = await self.enrich_projection(tconst)
        if enriched:
            return enriched

        payload = await self.ensure_core_projection(tconst)
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

    async def _maybe_enqueue_enrichment(
        self,
        tconst: str,
        row: dict[str, Any] | None,
        tmdb_id: int | None = None,
    ) -> bool:
        return await self.coordinator.maybe_enqueue(tconst, row, tmdb_id=tmdb_id)

    async def _schedule_local_enrichment(self, tconst: str, tmdb_id: int | None = None) -> bool:
        return await self.coordinator._schedule_local_enrichment(
            tconst,
            tmdb_id=tmdb_id,
        )

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
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
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

    async def enrich_projection(
        self,
        tconst: str,
        known_tmdb_id: int | None = None,
    ) -> dict[str, Any] | None:
        return await self.coordinator.enrich_projection(
            tconst,
            known_tmdb_id=known_tmdb_id,
        )

    async def requeue_stale_projections(self, batch_size: int = 500) -> int:
        """Mark ready projections past their staleness window as stale.

        Loops UPDATE ... LIMIT batch_size until affected rows < batch_size to
        bound InnoDB row-lock hold time. Safety cap of 100 iterations prevents
        pathological infinite loops.
        """
        max_iterations = 100
        total_affected = 0
        for _ in range(max_iterations):
            now = utcnow()
            affected = await self.db_pool.execute(
                """
                UPDATE movie_projection
                SET projection_state = %s
                WHERE projection_state = %s
                  AND stale_after IS NOT NULL
                  AND stale_after <= %s
                LIMIT %s
                """,
                [PROJECTION_STALE, PROJECTION_READY, now, batch_size],
                fetch="none",
            )
            affected_count = affected if isinstance(affected, int) else 0
            total_affected += affected_count
            if affected_count < batch_size:
                break
        return total_affected
