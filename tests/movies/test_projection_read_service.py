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
        from movies.projection_store import ProjectionReadService

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
        from movies.projection_store import ProjectionReadService

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
    async def test_await_inflight_promotes_core_row_to_ready_payload(self, monkeypatch):
        """When an in-flight task completes mid-wait and the row turns READY,
        the read path returns the freshly-enriched payload instead of the
        core placeholder.
        """
        from movies.projection_state import ProjectionState
        from movies.projection_store import ProjectionReadService

        monkeypatch.setenv("PROJECTION_RENDER_WAIT_SECONDS", "5")

        async def quick_inflight():
            await asyncio.sleep(0)
            return {"_full": True}

        inflight = asyncio.create_task(quick_inflight())

        core_row = {
            "tconst": "tt1",
            "tmdb_id": 1,
            "projection_state": ProjectionState.CORE.value,
        }
        ready_row = {
            "tconst": "tt1",
            "tmdb_id": 1,
            "projection_state": ProjectionState.READY.value,
        }

        repository = MagicMock()
        # First select_row returns CORE (initial fetch). Second call (after
        # awaiting inflight) returns READY.
        repository.select_row = AsyncMock(side_effect=[core_row, ready_row])
        repository.payload_from_row = MagicMock(
            return_value={"title": "Enriched", "projection_state": ProjectionState.READY.value}
        )

        coordinator = MagicMock()
        coordinator.get_inflight = MagicMock(return_value=inflight)
        coordinator.maybe_enqueue_if_not_inflight = AsyncMock(return_value=False)

        try:
            payload = await ProjectionReadService(
                repository=repository,
                coordinator=coordinator,
                enrich_projection=AsyncMock(),
            ).fetch_renderable_payload("tt1")
        finally:
            await asyncio.gather(inflight, return_exceptions=True)

        assert payload == {"title": "Enriched", "projection_state": ProjectionState.READY.value}
        assert repository.select_row.await_count == 2

    @pytest.mark.asyncio
    async def test_await_inflight_timeout_returns_none_and_keeps_task_alive(
        self, monkeypatch
    ):
        """A timeout during the bounded wait must NOT cancel the underlying
        inflight task — asyncio.shield protects the next reader's benefit.
        """
        from movies.projection_state import ProjectionState
        from movies.projection_store import ProjectionReadService

        monkeypatch.setenv("PROJECTION_RENDER_WAIT_SECONDS", "0.05")

        release = asyncio.Event()

        async def slow_inflight():
            await release.wait()
            return {"_full": True}

        inflight = asyncio.create_task(slow_inflight())
        core_row = {
            "tconst": "tt1",
            "tmdb_id": 1,
            "projection_state": ProjectionState.CORE.value,
        }
        repository = MagicMock()
        repository.select_row = AsyncMock(return_value=core_row)
        repository.payload_from_row = MagicMock(
            return_value={"title": "Core", "projection_state": ProjectionState.CORE.value}
        )
        repository.ensure_core_projection = AsyncMock(
            return_value={"title": "Core", "projection_state": ProjectionState.CORE.value}
        )
        coordinator = MagicMock()
        coordinator.get_inflight = MagicMock(return_value=inflight)
        coordinator.maybe_enqueue_if_not_inflight = AsyncMock(return_value=False)

        try:
            payload = await ProjectionReadService(
                repository=repository,
                coordinator=coordinator,
                enrich_projection=AsyncMock(),
            ).fetch_renderable_payload("tt1")

            # The underlying task is still running — shield protected it.
            assert not inflight.done()
        finally:
            release.set()
            await asyncio.gather(inflight, return_exceptions=True)

        assert payload == {"title": "Core", "projection_state": ProjectionState.CORE.value}

    @pytest.mark.asyncio
    async def test_await_inflight_exception_returns_none_and_falls_back(
        self, monkeypatch
    ):
        """If the in-flight task raises, the render path swallows the
        exception, logs at debug, and returns the core placeholder.
        """
        from movies.projection_state import ProjectionState
        from movies.projection_store import ProjectionReadService

        monkeypatch.setenv("PROJECTION_RENDER_WAIT_SECONDS", "5")

        async def failing_inflight():
            await asyncio.sleep(0)
            raise RuntimeError("tmdb 503")

        inflight = asyncio.create_task(failing_inflight())

        core_row = {
            "tconst": "tt1",
            "tmdb_id": 1,
            "projection_state": ProjectionState.CORE.value,
        }
        repository = MagicMock()
        repository.select_row = AsyncMock(return_value=core_row)
        repository.payload_from_row = MagicMock(
            return_value={"title": "Core", "projection_state": ProjectionState.CORE.value}
        )
        repository.ensure_core_projection = AsyncMock(
            return_value={"title": "Core", "projection_state": ProjectionState.CORE.value}
        )
        coordinator = MagicMock()
        coordinator.get_inflight = MagicMock(return_value=inflight)
        coordinator.maybe_enqueue_if_not_inflight = AsyncMock(return_value=False)

        # Drain the failed task so the await inside render-wait sees a
        # done task and re-raises into our broad except.
        await asyncio.gather(inflight, return_exceptions=True)

        payload = await ProjectionReadService(
            repository=repository,
            coordinator=coordinator,
            enrich_projection=AsyncMock(),
        ).fetch_renderable_payload("tt1")

        assert payload == {"title": "Core", "projection_state": ProjectionState.CORE.value}

    @pytest.mark.asyncio
    async def test_await_inflight_disabled_when_timeout_zero(self, monkeypatch):
        """PROJECTION_RENDER_WAIT_SECONDS=0 disables the bounded wait —
        get_inflight is never even consulted."""
        from movies.projection_state import ProjectionState
        from movies.projection_store import ProjectionReadService

        monkeypatch.setenv("PROJECTION_RENDER_WAIT_SECONDS", "0")

        core_row = {
            "tconst": "tt1",
            "tmdb_id": 1,
            "projection_state": ProjectionState.CORE.value,
        }
        repository = MagicMock()
        repository.select_row = AsyncMock(return_value=core_row)
        repository.payload_from_row = MagicMock(
            return_value={"title": "Core", "projection_state": ProjectionState.CORE.value}
        )
        repository.ensure_core_projection = AsyncMock(
            return_value={"title": "Core", "projection_state": ProjectionState.CORE.value}
        )
        coordinator = MagicMock()
        coordinator.get_inflight = MagicMock()
        coordinator.maybe_enqueue_if_not_inflight = AsyncMock(return_value=False)

        await ProjectionReadService(
            repository=repository,
            coordinator=coordinator,
            enrich_projection=AsyncMock(),
        ).fetch_renderable_payload("tt1")

        coordinator.get_inflight.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_row_enqueues_and_returns_existing_payload(self):
        from movies.projection_store import ProjectionReadService

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
