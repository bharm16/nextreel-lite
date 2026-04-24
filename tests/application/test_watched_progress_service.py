from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nextreel.application.watched_progress_service import WatchedEnrichmentProgressService


class Presenter:
    def normalize_movie(self, row, now):
        return {"tconst": row["tconst"], "title": row.get("primaryTitle", "Untitled")}


async def test_no_import_state_returns_done():
    service = WatchedEnrichmentProgressService()
    session_state = {}

    result = await service.progress(
        session_state=session_state,
        user_id="user-1",
        watched_store=AsyncMock(),
        presenter=Presenter(),
        now=datetime(2026, 1, 1),
    )

    assert result.done is True
    assert result.new_movies == []
    assert result.total == 0


async def test_all_sent_clears_session_state():
    service = WatchedEnrichmentProgressService()
    session_state = {
        "letterboxd_import_tconsts": ["tt1"],
        "letterboxd_sent_tconsts": ["tt1"],
        "letterboxd_enrichment_pending": True,
    }

    result = await service.progress(
        session_state=session_state,
        user_id="user-1",
        watched_store=AsyncMock(),
        presenter=Presenter(),
        now=datetime(2026, 1, 1),
    )

    assert result.done is True
    assert result.total_ready == 1
    assert result.total == 1
    assert session_state == {}


async def test_newly_ready_movies_are_returned_and_marked_sent():
    watched_store = SimpleNamespace(
        ready_tconsts_for_import=AsyncMock(return_value={"tt2"}),
        ready_import_rows=AsyncMock(
            return_value=[{"tconst": "tt2", "primaryTitle": "Ready Movie"}]
        ),
    )
    session_state = {
        "letterboxd_import_tconsts": ["tt1", "tt2"],
        "letterboxd_sent_tconsts": ["tt1"],
        "letterboxd_enrichment_pending": True,
    }
    service = WatchedEnrichmentProgressService()

    result = await service.progress(
        session_state=session_state,
        user_id="user-1",
        watched_store=watched_store,
        presenter=Presenter(),
        now=datetime(2026, 1, 1),
    )

    assert result.done is True
    assert result.new_movies == [{"tconst": "tt2", "title": "Ready Movie"}]
    assert result.new_count == 1
    assert result.total_ready == 2
    assert "letterboxd_import_tconsts" not in session_state
    assert "letterboxd_sent_tconsts" not in session_state
    assert "letterboxd_enrichment_pending" not in session_state


async def test_no_new_ready_keeps_session_pending():
    watched_store = SimpleNamespace(
        ready_tconsts_for_import=AsyncMock(return_value=set()),
        ready_import_rows=AsyncMock(),
    )
    session_state = {
        "letterboxd_import_tconsts": ["tt1", "tt2"],
        "letterboxd_sent_tconsts": ["tt1"],
        "letterboxd_enrichment_pending": True,
    }
    service = WatchedEnrichmentProgressService()

    result = await service.progress(
        session_state=session_state,
        user_id="user-1",
        watched_store=watched_store,
        presenter=Presenter(),
        now=datetime(2026, 1, 1),
    )

    assert result.done is False
    assert result.new_movies == []
    assert result.total_ready == 1
    assert session_state["letterboxd_import_tconsts"] == ["tt1", "tt2"]
    watched_store.ready_import_rows.assert_not_awaited()
