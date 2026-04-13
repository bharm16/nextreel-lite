from __future__ import annotations

import asyncio
import json
from typing import Any

from infra.metrics import enrichment_timeout_total
from infra.metrics_groups import safe_emit
from infra.time_utils import utcnow
from logging_config import get_logger
from movies.movie import Movie
from movies.projection_state import EnrichmentResult, ProjectionState

logger = get_logger(__name__)


class ProjectionPayloadDiffer:
    def persisted_payload_matches(self, *, store, existing, payload: dict[str, Any]) -> bool:
        if isinstance(existing, str):
            try:
                existing = json.loads(existing)
            except (TypeError, ValueError):
                existing = None
        if not isinstance(existing, dict):
            return False
        new_persisted = store._persisted_payload(payload)
        new_serialized = json.dumps(new_persisted, sort_keys=True)
        existing_serialized = json.dumps(existing, sort_keys=True)
        return new_serialized == existing_serialized


class ProjectionEnrichmentService:
    def __init__(
        self,
        *,
        store,
        tmdb_helper,
        timeout_seconds: float,
        payload_differ: ProjectionPayloadDiffer | None = None,
    ) -> None:
        self.store = store
        self.tmdb_helper = tmdb_helper
        self.timeout_seconds = timeout_seconds
        self.payload_differ = payload_differ or ProjectionPayloadDiffer()

    async def enrich_projection(
        self,
        tconst: str,
        known_tmdb_id: int | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow()
        row = await self.store.select_row(tconst)
        attempts = int(row.get("attempt_count", 0)) + 1 if row else 1
        tmdb_id = known_tmdb_id if known_tmdb_id is not None else (row or {}).get("tmdb_id")
        try:
            movie = Movie(tconst, self.store.db_pool, tmdb_helper=self.tmdb_helper)
            try:
                payload = await asyncio.wait_for(
                    movie.get_movie_data(known_tmdb_id=tmdb_id),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                safe_emit(enrichment_timeout_total.inc)
                raise RuntimeError("enrichment timeout after %ss" % self.timeout_seconds)
            if not payload:
                raise RuntimeError("TMDB enrichment returned no payload")

            payload["tmdb_id"] = payload.get("tmdb_id") or tmdb_id
            payload["projection_state"] = ProjectionState.READY.value

            if row and self.payload_differ.persisted_payload_matches(
                store=self.store,
                existing=row.get("payload_json"),
                payload=payload,
            ):
                logger.debug("payload unchanged for %s, refreshing metadata only", tconst)
                await self.store.apply_enrichment_result(
                    tconst,
                    EnrichmentResult(
                        status="ready",
                        persistence_mode="READY_METADATA_ONLY",
                        payload=payload,
                        attempts=attempts,
                        tmdb_id=payload.get("tmdb_id"),
                        error=None,
                        timestamp=now,
                    ),
                )
                return payload

            await self.store.apply_enrichment_result(
                tconst,
                EnrichmentResult(
                    status="ready",
                    persistence_mode="READY_UPSERT",
                    payload=payload,
                    attempts=attempts,
                    tmdb_id=payload.get("tmdb_id"),
                    error=None,
                    timestamp=now,
                ),
            )
            return payload
        except Exception as exc:
            core_payload = await self.store.ensure_core_projection(tconst)
            await self.store.apply_enrichment_result(
                tconst,
                EnrichmentResult(
                    status="failed",
                    persistence_mode="FAILED_UPSERT",
                    payload=core_payload or {},
                    attempts=attempts,
                    tmdb_id=tmdb_id,
                    error=str(exc),
                    timestamp=now,
                ),
            )
            logger.warning("Projection enrichment failed for %s: %s", tconst, exc)
            return core_payload
