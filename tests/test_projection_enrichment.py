import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from movies.projection_enrichment import ProjectionEnrichmentCoordinator
from movies.projection_store import ProjectionStore


@pytest.mark.asyncio
async def test_maybe_enqueue_passes_dedup_job_id():
    store = MagicMock()
    store.mark_attempt = AsyncMock()
    store.select_row = AsyncMock(return_value=None)

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


@pytest.mark.asyncio
async def test_enrich_projection_times_out_and_marks_failed():
    """asyncio.wait_for around movie.get_movie_data routes timeout -> _upsert_failed."""
    store = MagicMock()
    store.db_pool = MagicMock()
    store.select_row = AsyncMock(return_value=None)
    store.upsert_ready = AsyncMock()
    store.upsert_failed = AsyncMock()
    store.ensure_core_projection = AsyncMock(return_value={"title": "core"})

    coord = ProjectionEnrichmentCoordinator(
        store=store, tmdb_helper=MagicMock(), enqueue_fn=None
    )
    coord.ENRICHMENT_TIMEOUT_SECONDS = 0.05

    async def _slow(*args, **kwargs):
        await asyncio.sleep(10)

    with patch("movies.projection_enrichment.Movie") as MockMovie:
        MockMovie.return_value.get_movie_data = AsyncMock(side_effect=_slow)
        result = await coord.enrich_projection("tt1")

    store.upsert_ready.assert_not_awaited()
    store.upsert_failed.assert_awaited_once()
    args, kwargs = store.upsert_failed.call_args
    # error string is positional arg index 4 in upsert_failed call signature
    # (tconst, core_payload, now, attempts, error, tmdb_id=...)
    assert "timeout" in args[4].lower()
    assert result == {"title": "core"}


@pytest.mark.asyncio
async def test_schedule_local_enrichment_drops_when_backlog_full():
    """Backlog cap prevents new schedules above max_pending and increments metric."""
    store = MagicMock()
    coord = ProjectionEnrichmentCoordinator(
        store=store, tmdb_helper=MagicMock(), enqueue_fn=None, max_pending=2
    )
    # Pre-populate dedupe set with 2 entries.
    coord._local_enrichment_tconsts.add("tt_a")
    coord._local_enrichment_tconsts.add("tt_b")

    with patch("movies.projection_enrichment.enrichment_backlog_drop_total") as mock_metric:
        result = await coord._schedule_local_enrichment("tt_new")

    assert result is False
    assert "tt_new" not in coord._local_enrichment_tconsts
    assert len(coord._local_enrichment_tasks) == 0
    mock_metric.inc.assert_called_once()


@pytest.mark.asyncio
async def test_enrich_projection_timeout_increments_metric():
    """asyncio.TimeoutError path should increment enrichment_timeout_total."""
    store = MagicMock()
    store.db_pool = MagicMock()
    store.select_row = AsyncMock(return_value=None)
    store.upsert_ready = AsyncMock()
    store.upsert_failed = AsyncMock()
    store.ensure_core_projection = AsyncMock(return_value={"title": "core"})

    coord = ProjectionEnrichmentCoordinator(
        store=store, tmdb_helper=MagicMock(), enqueue_fn=None
    )
    coord.ENRICHMENT_TIMEOUT_SECONDS = 0.05

    async def _slow(*args, **kwargs):
        await asyncio.sleep(10)

    with (
        patch("movies.projection_enrichment.Movie") as MockMovie,
        patch("movies.projection_enrichment.enrichment_timeout_total") as mock_metric,
    ):
        MockMovie.return_value.get_movie_data = AsyncMock(side_effect=_slow)
        await coord.enrich_projection("tt1")

    mock_metric.inc.assert_called_once()


@pytest.mark.asyncio
async def test_enrich_projection_skips_upsert_when_payload_unchanged():
    """When the freshly-fetched payload is identical to the stored one,
    enrich_projection should call refresh_ready_metadata instead of
    upsert_ready, avoiding the large payload UPDATE."""
    new_payload = {
        "title": "Same",
        "tmdb_id": 42,
        "poster_url": "/x.jpg",
        "_full": True,
    }
    # The persisted shape strips images/credits — same shape goes in.
    persisted = ProjectionStore._persisted_payload(new_payload)
    persisted_with_state = dict(persisted)
    persisted_with_state["projection_state"] = "ready"
    existing_json = json.dumps(persisted_with_state, sort_keys=True)

    store = MagicMock()
    store.db_pool = MagicMock()
    store._persisted_payload = ProjectionStore._persisted_payload
    store.select_row = AsyncMock(return_value={
        "tconst": "tt1",
        "tmdb_id": 42,
        "payload_json": existing_json,
        "projection_state": "stale",
        "attempt_count": 1,
    })
    store.upsert_ready = AsyncMock()
    store.upsert_failed = AsyncMock()
    store.refresh_ready_metadata = AsyncMock()
    store.ensure_core_projection = AsyncMock()

    coord = ProjectionEnrichmentCoordinator(
        store=store, tmdb_helper=MagicMock(), enqueue_fn=None
    )

    with patch("movies.projection_enrichment.Movie") as MockMovie:
        MockMovie.return_value.get_movie_data = AsyncMock(
            return_value=dict(new_payload)
        )
        result = await coord.enrich_projection("tt1")

    store.upsert_ready.assert_not_awaited()
    store.refresh_ready_metadata.assert_awaited_once()
    args, kwargs = store.refresh_ready_metadata.call_args
    assert args[0] == "tt1"
    assert result["title"] == "Same"
