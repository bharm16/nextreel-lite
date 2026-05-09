from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_projection_enrichment_service_colocated_with_coordinator():
    from movies.projection_enrichment import ProjectionEnrichmentService

    assert ProjectionEnrichmentService.__module__ == "movies.projection_enrichment"


def test_projection_payload_differ_colocated_with_coordinator():
    from movies.projection_enrichment import ProjectionPayloadDiffer

    assert ProjectionPayloadDiffer.__module__ == "movies.projection_enrichment"


@pytest.mark.asyncio
async def test_projection_coordinator_delegates_enrichment_execution(monkeypatch):
    from movies.projection_enrichment import ProjectionEnrichmentCoordinator

    store = MagicMock()
    service = MagicMock()
    service.enrich_projection = AsyncMock(return_value={"title": "Ready"})
    coordinator = ProjectionEnrichmentCoordinator(store, tmdb_helper=MagicMock())
    coordinator._enrichment_service = service

    result = await coordinator.enrich_projection("tt1", known_tmdb_id=123)

    assert result == {"title": "Ready"}
    # Coordinator now passes tmdb_helper/timeout_seconds per-call instead of
    # mutating shared service state.
    service.enrich_projection.assert_awaited_once()
    args, kwargs = service.enrich_projection.call_args
    assert args == ("tt1",)
    assert kwargs["known_tmdb_id"] == 123
    assert kwargs["tmdb_helper"] is coordinator.tmdb_helper
    assert kwargs["timeout_seconds"] == coordinator.ENRICHMENT_TIMEOUT_SECONDS
