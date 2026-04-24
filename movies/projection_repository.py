"""Persistence and row-mapping helpers for projections."""

from __future__ import annotations

import json
from datetime import datetime, date
from decimal import Decimal
from typing import Any

from infra.time_utils import utcnow
from movies.projection_state import (
    FAILED_RETRY_COOLDOWN,
    EnrichmentResult,
    ProjectionState,
    STALE_AFTER,
)

PLACEHOLDER_POSTER = "/static/img/poster-placeholder.svg"
PLACEHOLDER_BACKDROP = "/static/img/backdrop-placeholder.svg"


def _json_default(value: Any) -> Any:
    # aiomysql returns DECIMAL columns (averageRating, numVotes sums, etc.)
    # as Decimal and DATE columns as date — neither are JSON-serializable by
    # default, so every projection upsert silently failed.
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _dumps(obj: Any, **kwargs: Any) -> str:
    return json.dumps(obj, default=_json_default, **kwargs)


class ProjectionRepository:
    def __init__(self, db_pool):
        self.db_pool = db_pool

    # ------------------------------------------------------------------
    # Payload shaping helpers (absorbed from projection_payload_factory).
    # These are data-mapping concerns that belong with the row model.
    # ------------------------------------------------------------------

    def payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload_json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("projection_state", row.get("projection_state"))
        payload.setdefault("tconst", row.get("tconst"))
        return payload

    @staticmethod
    def persisted_payload(payload: dict[str, Any]) -> dict[str, Any]:
        slimmed = dict(payload)
        slimmed.pop("images", None)
        slimmed.pop("credits", None)
        return slimmed

    def build_core_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        language = row.get("language") or "unknown"
        genres = row.get("genres") or "Unknown"
        rating = row.get("averageRating") or 0
        votes = row.get("numVotes") or 0
        return {
            "title": row.get("primaryTitle") or "Unknown",
            "tconst": row["tconst"],
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
            "projection_state": ProjectionState.CORE.value,
        }

    # ------------------------------------------------------------------
    # SQL persistence.
    # ------------------------------------------------------------------

    async def select_row(self, tconst: str) -> dict[str, Any] | None:
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

    async def fetch_renderable_payloads(
        self,
        tconsts: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not tconsts:
            return {}
        unique = list(dict.fromkeys(tconsts))
        placeholders = ",".join(["%s"] * len(unique))
        sql = f"""
            SELECT tconst, tmdb_id, payload_json, projection_state,
                   enriched_at, stale_after, last_attempt_at, attempt_count, last_error
            FROM movie_projection
            WHERE tconst IN ({placeholders})
        """
        rows = await self.db_pool.execute(sql, unique, fetch="all")
        if not rows:
            return {}
        return {row["tconst"]: self.payload_from_row(row) for row in rows}

    async def mark_ready_stale_if_due(self, tconst: str) -> None:
        await self.db_pool.execute(
            """
            UPDATE movie_projection
            SET projection_state = %s
            WHERE tconst = %s AND projection_state = %s
            """,
            [ProjectionState.STALE.value, tconst, ProjectionState.READY.value],
            fetch="none",
        )

    async def mark_attempt(self, tconst: str, now: datetime) -> None:
        await self.db_pool.execute(
            """
            UPDATE movie_projection
            SET last_attempt_at = %s
            WHERE tconst = %s
            """,
            [now, tconst],
            fetch="none",
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
                    WHEN movie_projection.projection_state IN (%s, %s) THEN movie_projection.payload_json
                    ELSE new_row.payload_json
                END,
                projection_state = CASE
                    WHEN movie_projection.projection_state IN (%s, %s) THEN movie_projection.projection_state
                    ELSE new_row.projection_state
                END,
                last_attempt_at = COALESCE(movie_projection.last_attempt_at, %s)
            """,
            [
                tconst,
                None,
                _dumps(self.persisted_payload(payload)),
                ProjectionState.CORE.value,
                ProjectionState.READY.value,
                ProjectionState.STALE.value,
                ProjectionState.READY.value,
                ProjectionState.STALE.value,
                now,
            ],
            fetch="none",
        )
        return payload

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

    async def upsert_ready(
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
    ) -> None:
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
                _dumps(self.persisted_payload(payload)),
                ProjectionState.READY.value,
                now,
                now + STALE_AFTER,
                now,
                attempts,
            ],
            fetch="none",
        )

    async def refresh_ready_metadata(
        self,
        tconst: str,
        now: datetime,
        attempts: int,
    ) -> None:
        await self.db_pool.execute(
            """
            UPDATE movie_projection
            SET projection_state = %s,
                enriched_at = %s,
                stale_after = %s,
                last_attempt_at = %s,
                attempt_count = %s,
                last_error = NULL
            WHERE tconst = %s
            """,
            [
                ProjectionState.READY.value,
                now,
                now + STALE_AFTER,
                now,
                attempts,
                tconst,
            ],
            fetch="none",
        )

    async def upsert_failed(
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
        error: str,
        tmdb_id: int | None = None,
    ) -> None:
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
                payload_json = COALESCE(movie_projection.payload_json, new_row.payload_json)
            """,
            [
                tconst,
                tmdb_id,
                _dumps(self.persisted_payload(payload)),
                ProjectionState.FAILED.value,
                now,
                attempts,
                error,
            ],
            fetch="none",
        )

    async def apply_enrichment_result(
        self,
        tconst: str,
        result: EnrichmentResult,
    ) -> None:
        if result.persistence_mode == "READY_UPSERT":
            await self.upsert_ready(
                tconst,
                result.payload,
                result.timestamp,
                result.attempts,
            )
            return
        if result.persistence_mode == "READY_METADATA_ONLY":
            await self.refresh_ready_metadata(
                tconst,
                result.timestamp,
                result.attempts,
            )
            return
        if result.persistence_mode == "FAILED_UPSERT":
            await self.upsert_failed(
                tconst,
                result.payload,
                result.timestamp,
                result.attempts,
                result.error or "",
                tmdb_id=result.tmdb_id,
            )
            return
        raise ValueError(f"Unknown enrichment persistence mode: {result.persistence_mode}")

    async def requeue_stale_projections(self, batch_size: int = 500) -> int:
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
                [ProjectionState.STALE.value, ProjectionState.READY.value, now, batch_size],
                fetch="none",
            )
            affected_count = affected if isinstance(affected, int) else 0
            total_affected += affected_count
            if affected_count < batch_size:
                break

        for _ in range(max_iterations):
            cutoff = utcnow() - FAILED_RETRY_COOLDOWN
            affected = await self.db_pool.execute(
                """
                UPDATE movie_projection
                SET projection_state = %s
                WHERE projection_state = %s
                  AND (last_attempt_at IS NULL OR last_attempt_at <= %s)
                LIMIT %s
                """,
                [ProjectionState.STALE.value, ProjectionState.FAILED.value, cutoff, batch_size],
                fetch="none",
            )
            affected_count = affected if isinstance(affected, int) else 0
            total_affected += affected_count
            if affected_count < batch_size:
                break
        return total_affected
