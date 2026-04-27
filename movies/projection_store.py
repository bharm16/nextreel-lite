"""Projection manager: facade that coordinates persistence, enrichment, and read-path policy.

This module owns the public ``ProjectionStore`` facade plus the read-path
decision logic (``ProjectionReadService``). Persistence lives in
:mod:`movies.projection_repository` and enrichment scheduling/execution lives
in :mod:`movies.projection_enrichment`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from infra.time_utils import env_bool, env_float, utcnow
from logging_config import get_logger
from movies.projection_enrichment import ProjectionEnrichmentCoordinator
from movies.projection_repository import (
    PLACEHOLDER_BACKDROP,
    PLACEHOLDER_POSTER,
    ProjectionRepository,
)
from movies.projection_state import (
    EnrichmentResult,
    ProjectionState,
)

logger = get_logger(__name__)

# Backward-compatible string constants (used by tests and callers that
# predate the enum).
PROJECTION_READY = ProjectionState.READY.value
PROJECTION_STALE = ProjectionState.STALE.value
PROJECTION_CORE = ProjectionState.CORE.value
PROJECTION_FAILED = ProjectionState.FAILED.value

__all__ = [
    "ProjectionStore",
    "ProjectionReadService",
    "PLACEHOLDER_BACKDROP",
    "PLACEHOLDER_POSTER",
    "PROJECTION_READY",
    "PROJECTION_STALE",
    "PROJECTION_CORE",
    "PROJECTION_FAILED",
]


def _enrichment_blocks_render() -> bool:
    return env_bool("PROJECTION_ENRICHMENT_BLOCKS_RENDER", default=False)


def _render_inflight_wait_seconds() -> float:
    """Bounded-wait timeout for an in-flight enrichment on the render path.

    A small wait (default 1.0s) gives a TMDb fetch started by the redirect
    prefetch a chance to finish before we render the core placeholder. Set
    to 0 to disable. ``PROJECTION_ENRICHMENT_BLOCKS_RENDER=true`` already
    blocks unconditionally, so this knob is irrelevant in that mode.
    """
    return env_float("PROJECTION_RENDER_WAIT_SECONDS", default=1.0)


class ProjectionReadService:
    """Read-path policy: decide whether to serve, mark stale, or enqueue.

    Absorbed from ``movies.projection_read_service``. Kept as a distinct
    class so the decision tree is easy to unit-test in isolation. The
    store delegates every read through an instance of this service.
    """

    def __init__(self, *, repository, coordinator, enrich_projection):
        self.repository = repository
        self.coordinator = coordinator
        self._enrich_projection = enrich_projection

    async def fetch_renderable_payload(self, tconst: str):
        # Never wait for pending enrichment on the render path: HTML delivery
        # is what lets the browser discover the hero image.
        row = await self.repository.select_row(tconst)
        now = utcnow()
        if row:
            state = row["projection_state"]
            stale_after = row.get("stale_after")
            if state == ProjectionState.READY.value and stale_after and stale_after <= now:
                await self.repository.mark_ready_stale_if_due(tconst)
                row["projection_state"] = ProjectionState.STALE.value
                state = ProjectionState.STALE.value

            if state == ProjectionState.READY.value:
                return self.repository.payload_from_row(row)

            if state == ProjectionState.STALE.value:
                if self.coordinator is not None:
                    await self.coordinator.maybe_enqueue_if_not_inflight(
                        tconst,
                        row,
                        tmdb_id=row.get("tmdb_id"),
                    )
                return self.repository.payload_from_row(row)

            if ProjectionState(state).needs_enrichment():
                if _enrichment_blocks_render():
                    enriched = await self._enrich_projection(
                        tconst,
                        known_tmdb_id=row.get("tmdb_id"),
                    )
                    if enriched:
                        return enriched
                else:
                    if self.coordinator is not None:
                        await self.coordinator.maybe_enqueue_if_not_inflight(
                            tconst,
                            row,
                            tmdb_id=row.get("tmdb_id"),
                        )
                        promoted = await self._await_inflight_for_render(tconst)
                        if promoted is not None:
                            return promoted

                payload = self.repository.payload_from_row(row)
                if not payload or payload.get("projection_state") == ProjectionState.FAILED.value:
                    payload = await self.repository.ensure_core_projection(tconst)
                return payload

        if _enrichment_blocks_render():
            enriched = await self._enrich_projection(tconst)
            if enriched:
                return enriched

        payload = await self.repository.ensure_core_projection(tconst)
        if not _enrichment_blocks_render() and self.coordinator is not None and payload:
            await self.coordinator.maybe_enqueue_if_not_inflight(
                tconst,
                None,
                tmdb_id=None,
            )
            promoted = await self._await_inflight_for_render(tconst)
            if promoted is not None:
                return promoted
        return payload

    async def _await_inflight_for_render(self, tconst: str) -> dict[str, Any] | None:
        """Briefly wait for an in-flight enrichment, then re-read the row.

        When a navigation redirect handler kicks off ``get_or_start_inflight``
        and the user follows the redirect quickly, that task is still
        running by the time this read fires. Awaiting it for a small window
        lets us serve READY data instead of the core placeholder for the
        common back-to-back-clicks case. We ``asyncio.shield`` the task so
        a timeout here does NOT cancel the underlying enrichment — it
        keeps running and benefits the next reader.

        Shield safety relies on the task being retained somewhere besides
        this awaiter so a TimeoutError here doesn't drop the only strong
        reference. The coordinator provides two retention paths:
        ``_inflight_enrichment[tconst]`` until the task's ``finally``
        clears it, and either the app-level ``background_tasks`` set (when
        a scheduler is wired) or ``_local_enrichment_tasks`` (fallback) —
        registered inside ``get_or_start_inflight``. Removing both would
        make this shield ineffective.
        """
        if self.coordinator is None:
            return None
        timeout = _render_inflight_wait_seconds()
        if timeout <= 0:
            return None
        task = self.coordinator.get_inflight(tconst)
        if task is None:
            return None
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        except Exception:
            logger.debug(
                "Inflight enrichment for %s raised during render-wait; "
                "falling back to placeholder",
                tconst,
                exc_info=True,
            )
            return None
        row = await self.repository.select_row(tconst)
        if row and row.get("projection_state") == ProjectionState.READY.value:
            return self.repository.payload_from_row(row)
        return None


class ProjectionStore:
    def __init__(self, db_pool, tmdb_helper=None, enqueue_fn=None):
        self.db_pool = db_pool
        self.repository = ProjectionRepository(db_pool)
        self.coordinator = ProjectionEnrichmentCoordinator(
            self,
            tmdb_helper=tmdb_helper,
            enqueue_fn=enqueue_fn,
        )
        self.read_service = ProjectionReadService(
            repository=self,
            coordinator=self.coordinator,
            enrich_projection=self.enrich_projection,
        )

    def attach_coordinator(
        self,
        coordinator: ProjectionEnrichmentCoordinator,
    ) -> ProjectionEnrichmentCoordinator:
        coordinator.store = self
        self.coordinator = coordinator
        self.read_service.coordinator = coordinator
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

    def payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.repository.payload_from_row(row)

    async def select_row(self, tconst: str) -> dict[str, Any] | None:
        return await self.repository.select_row(tconst)

    async def fetch_renderable_payload(self, tconst: str) -> dict[str, Any] | None:
        return await self.read_service.fetch_renderable_payload(tconst)

    async def fetch_renderable_payloads(
        self, tconsts: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Batched variant of :meth:`fetch_renderable_payload`.

        Returns a dict mapping ``tconst`` to its payload for every tconst
        that has a projection row in any state. Tconsts without a row are
        absent from the result. Unlike the per-tconst variant this does
        NOT enrich, mark stale, or enqueue work — it is a pure read for
        callers that already have a batch of refs and just need the
        renderable shape.
        """
        if not tconsts:
            return {}
        return await self.repository.fetch_renderable_payloads(tconsts)

    @staticmethod
    def _persisted_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return ProjectionRepository.persisted_payload(payload)

    async def mark_attempt(self, tconst: str, now: datetime) -> None:
        await self.repository.mark_attempt(tconst, now)

    async def mark_ready_stale_if_due(self, tconst: str) -> None:
        await self.repository.mark_ready_stale_if_due(tconst)

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
        return await self.repository.ensure_core_projection(tconst)

    def build_core_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.repository.build_core_payload(row)

    async def ready_check(self) -> bool:
        return await self.repository.ready_check()

    async def upsert_ready(
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
    ) -> None:
        await self.repository.upsert_ready(tconst, payload, now, attempts)

    async def refresh_ready_metadata(
        self,
        tconst: str,
        now: datetime,
        attempts: int,
    ) -> None:
        await self.repository.refresh_ready_metadata(tconst, now, attempts)

    async def upsert_failed(
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
        error: str,
        tmdb_id: int | None = None,
    ) -> None:
        await self.repository.upsert_failed(
            tconst,
            payload,
            now,
            attempts,
            error,
            tmdb_id=tmdb_id,
        )

    async def apply_enrichment_result(
        self,
        tconst: str,
        result: EnrichmentResult,
    ) -> None:
        await self.repository.apply_enrichment_result(tconst, result)

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
        return await self.repository.requeue_stale_projections(batch_size=batch_size)
