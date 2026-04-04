"""Projection enrichment orchestration and local task lifecycle."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from infra.time_utils import utcnow
from logging_config import get_logger
from movies.movie import Movie
from movies.projection_state import ENQUEUE_COOLDOWN, ProjectionState

if TYPE_CHECKING:
    from movies.projection_store import ProjectionStore

logger = get_logger(__name__)


class ProjectionEnrichmentCoordinator:
    """Owns enrichment orchestration outside the persistence layer."""

    def __init__(
        self,
        store: "ProjectionStore",
        tmdb_helper=None,
        enqueue_fn=None,
    ) -> None:
        self.store = store
        self.tmdb_helper = tmdb_helper
        self.enqueue_fn = enqueue_fn
        self._local_enrichment_tconsts: set[str] = set()
        self._local_enrichment_tasks: set[asyncio.Task] = set()

    async def maybe_enqueue(
        self,
        tconst: str,
        row: dict[str, Any] | None,
        tmdb_id: int | None = None,
    ) -> bool:
        """Try to enqueue background enrichment."""
        now = utcnow()
        last_attempt_at = row.get("last_attempt_at") if row else None
        if last_attempt_at and now < last_attempt_at + ENQUEUE_COOLDOWN:
            return False

        if self.enqueue_fn:
            try:
                result = await self.enqueue_fn("enrich_projection", tconst, tmdb_id)
                if result is not None:
                    await self.store._mark_attempt(tconst, now)
                    return True
            except Exception as exc:
                logger.debug("Failed to enqueue enrich_projection(%s): %s", tconst, exc)

        scheduled = await self._schedule_local_enrichment(tconst, tmdb_id=tmdb_id)
        if scheduled:
            await self.store._mark_attempt(tconst, now)
        return scheduled

    async def _schedule_local_enrichment(
        self,
        tconst: str,
        tmdb_id: int | None = None,
    ) -> bool:
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

    async def enrich_projection(
        self,
        tconst: str,
        known_tmdb_id: int | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow()
        row = await self.store._select_row(tconst)
        attempts = int(row.get("attempt_count", 0)) + 1 if row else 1
        tmdb_id = known_tmdb_id if known_tmdb_id is not None else (row or {}).get("tmdb_id")
        try:
            movie = Movie(tconst, self.store.db_pool, tmdb_helper=self.tmdb_helper)
            payload = await movie.get_movie_data(known_tmdb_id=tmdb_id)
            if not payload:
                raise RuntimeError("TMDB enrichment returned no payload")

            payload["tmdb_id"] = payload.get("tmdb_id") or tmdb_id
            payload["projection_state"] = ProjectionState.READY.value
            await self.store._upsert_ready(tconst, payload, now, attempts)
            return payload
        except Exception as exc:
            core_payload = await self.store.ensure_core_projection(tconst)
            await self.store._upsert_failed(
                tconst,
                core_payload or {},
                now,
                attempts,
                str(exc),
                tmdb_id=tmdb_id,
            )
            logger.warning("Projection enrichment failed for %s: %s", tconst, exc)
            return core_payload

    async def drain_pending(self, timeout: float = 5.0) -> None:
        tasks = list(self._local_enrichment_tasks)
        if not tasks:
            return

        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Timed out draining %d local enrichment task(s)", len(tasks))
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self, timeout: float = 5.0) -> None:
        await self.drain_pending(timeout=timeout)
