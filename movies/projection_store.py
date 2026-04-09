"""Projection-table rendering source and async enrichment hooks."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from infra.time_utils import utcnow
from logging_config import get_logger
from movies.projection_enrichment import ProjectionEnrichmentCoordinator
from movies.projection_payload_factory import (
    PLACEHOLDER_BACKDROP,
    PLACEHOLDER_POSTER,
    ProjectionPayloadFactory,
)
from movies.projection_read_service import ProjectionReadService
from movies.projection_repository import ProjectionRepository
from movies.projection_results import EnrichmentResult
from movies.projection_state import (
    FAILED_RETRY_COOLDOWN,
    STALE_AFTER,
    ProjectionState,
)

if TYPE_CHECKING:
    import asyncio

logger = get_logger(__name__)

# Backward-compatible string constants (used by tests and callers).
PROJECTION_READY = ProjectionState.READY.value
PROJECTION_STALE = ProjectionState.STALE.value
PROJECTION_CORE = ProjectionState.CORE.value
PROJECTION_FAILED = ProjectionState.FAILED.value


class ProjectionStore:
    def __init__(self, db_pool, tmdb_helper=None, enqueue_fn=None):
        self.db_pool = db_pool
        self.payload_factory = ProjectionPayloadFactory()
        self.repository = ProjectionRepository(db_pool, payload_factory=self.payload_factory)
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

    def _payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.repository.payload_from_row(row)

    def payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._payload_from_row(row)

    async def _select_row(self, tconst: str) -> dict[str, Any] | None:
        return await self.repository.select_row(tconst)

    async def select_row(self, tconst: str) -> dict[str, Any] | None:
        """Public accessor for the row-select implementation.

        Delegates to :meth:`_select_row` so tests that patch the private
        name continue to work transparently.
        """
        return await self._select_row(tconst)

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
        return ProjectionPayloadFactory.persisted_payload(payload)

    async def _mark_attempt(self, tconst: str, now: datetime) -> None:
        await self.repository.mark_attempt(tconst, now)

    async def mark_attempt(self, tconst: str, now: datetime) -> None:
        """Public accessor for the mark-attempt implementation."""
        await self._mark_attempt(tconst, now)

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
        return self.payload_factory.build_core_payload(row)

    async def ready_check(self) -> bool:
        return await self.repository.ready_check()

    async def _upsert_ready(
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
    ) -> None:
        await self.repository.upsert_ready(tconst, payload, now, attempts)

    async def upsert_ready(
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
    ) -> None:
        """Public accessor for the upsert-ready implementation."""
        await self._upsert_ready(tconst, payload, now, attempts)

    async def refresh_ready_metadata(
        self,
        tconst: str,
        now: datetime,
        attempts: int,
    ) -> None:
        await self.repository.refresh_ready_metadata(tconst, now, attempts)

    async def _upsert_failed(
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

    async def upsert_failed(
        self,
        tconst: str,
        payload: dict[str, Any],
        now: datetime,
        attempts: int,
        error: str,
        tmdb_id: int | None = None,
    ) -> None:
        """Public accessor for the upsert-failed implementation."""
        await self._upsert_failed(
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
