from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_projection_enrichment_service_lives_outside_coordinator():
    from movies.projection_enrichment_service import ProjectionEnrichmentService

    assert ProjectionEnrichmentService.__module__ == "movies.projection_enrichment_service"


def test_projection_payload_differ_lives_outside_coordinator():
    from movies.projection_enrichment_service import ProjectionPayloadDiffer

    assert ProjectionPayloadDiffer.__module__ == "movies.projection_enrichment_service"


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
    service.enrich_projection.assert_awaited_once_with("tt1", known_tmdb_id=123)
