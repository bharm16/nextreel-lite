"""Unit tests for the extracted projection read service."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from movies.projection_store import PROJECTION_READY, PROJECTION_STALE


class TestProjectionReadService:
    @pytest.mark.asyncio
    async def test_pending_inflight_does_not_block_ready_row_return(self):
        from movies.projection_read_service import ProjectionReadService

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_inflight():
            started.set()
            await release.wait()
            return {"title": "Enriched", "projection_state": PROJECTION_READY}

        inflight = asyncio.create_task(slow_inflight())
        await started.wait()

        repository = MagicMock()
        repository.select_row = AsyncMock(
            return_value={
                "tconst": "tt1234567",
                "projection_state": PROJECTION_READY,
                "stale_after": datetime(2099, 1, 1),
            }
        )
        repository.payload_from_row = MagicMock(
            return_value={"title": "Ready", "projection_state": PROJECTION_READY}
        )
        coordinator = MagicMock()
        coordinator.get_inflight = MagicMock(return_value=inflight)

        try:
            payload = await asyncio.wait_for(
                ProjectionReadService(
                    repository=repository,
                    coordinator=coordinator,
                    enrich_projection=AsyncMock(),
                ).fetch_renderable_payload("tt1234567"),
                timeout=0.05,
            )
        finally:
            release.set()
            await asyncio.gather(inflight, return_exceptions=True)

        assert payload == {"title": "Ready", "projection_state": PROJECTION_READY}
        repository.select_row.assert_awaited_once_with("tt1234567")

    @pytest.mark.asyncio
    async def test_ready_row_returns_payload_without_enqueue(self):
        from movies.projection_read_service import ProjectionReadService

        repository = MagicMock()
        repository.select_row = AsyncMock(
            return_value={
                "tconst": "tt1234567",
                "projection_state": PROJECTION_READY,
                "stale_after": datetime(2099, 1, 1),
            }
        )
        repository.payload_from_row = MagicMock(
            return_value={"title": "Ready", "projection_state": PROJECTION_READY}
        )
        coordinator = MagicMock()
        coordinator.get_inflight = MagicMock(return_value=None)

        payload = await ProjectionReadService(
            repository=repository,
            coordinator=coordinator,
            enrich_projection=AsyncMock(),
        ).fetch_renderable_payload("tt1234567")

        assert payload == {"title": "Ready", "projection_state": PROJECTION_READY}
        coordinator.maybe_enqueue_if_not_inflight.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_row_enqueues_and_returns_existing_payload(self):
        from movies.projection_read_service import ProjectionReadService

        row = {
            "tconst": "tt1234567",
            "tmdb_id": 42,
            "projection_state": PROJECTION_STALE,
            "stale_after": datetime(2099, 1, 1),
        }
        repository = MagicMock()
        repository.select_row = AsyncMock(return_value=row)
        repository.payload_from_row = MagicMock(
            return_value={"title": "Stale", "projection_state": PROJECTION_STALE}
        )
        coordinator = MagicMock()
        coordinator.get_inflight = MagicMock(return_value=None)
        coordinator.maybe_enqueue_if_not_inflight = AsyncMock(return_value=True)

        payload = await ProjectionReadService(
            repository=repository,
            coordinator=coordinator,
            enrich_projection=AsyncMock(),
        ).fetch_renderable_payload("tt1234567")

        assert payload == {"title": "Stale", "projection_state": PROJECTION_STALE}
        coordinator.maybe_enqueue_if_not_inflight.assert_awaited_once_with(
            "tt1234567",
            row,
            tmdb_id=42,
        )
