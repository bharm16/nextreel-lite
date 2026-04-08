import pytest
from unittest.mock import AsyncMock, MagicMock

from movies.projection_enrichment import ProjectionEnrichmentCoordinator


@pytest.mark.asyncio
async def test_maybe_enqueue_passes_dedup_job_id():
    store = MagicMock()
    store._mark_attempt = AsyncMock()
    store._select_row = AsyncMock(return_value=None)

    enqueue_fn = AsyncMock(return_value=object())
    coord = ProjectionEnrichmentCoordinator(
        store=store, tmdb_helper=None, enqueue_fn=enqueue_fn
    )

    await coord.maybe_enqueue("tt9999", row=None, tmdb_id=42)

    enqueue_fn.assert_awaited_once()
    args, kwargs = enqueue_fn.call_args
    assert args[0] == "enrich_projection"
    assert args[1] == "tt9999"
    assert args[2] == 42
    assert kwargs.get("_job_id") == "enrich:tt9999"
