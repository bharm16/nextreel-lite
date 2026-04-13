# SRP/SoC Phase 1 Watched Letterboxd Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract Letterboxd watched import and enrichment progress workflow out of route handlers while preserving current route behavior.

**Architecture:** Keep `nextreel/web/routes/watched.py` as HTTP glue. Move import workflow into `nextreel/application/letterboxd_import_service.py`. Move progress polling workflow into `nextreel/application/watched_progress_service.py`. Add repository methods to `movies/watched_store.py` so routes no longer issue direct SQL for progress polling.

**Tech Stack:** Python 3.11, Quart, pytest, AsyncMock, existing `movies.letterboxd_import`, existing `WatchedStore`, Jinja templates.

---

## File Structure

- Create `nextreel/application/letterboxd_import_service.py`: structured outcomes and import workflow.
- Create `nextreel/application/watched_progress_service.py`: structured progress outcome and session-state mutation.
- Modify `movies/watched_store.py`: add ready-import row lookup used by progress service.
- Modify `nextreel/web/route_services.py`: expose public watched-row normalization for partial rendering.
- Modify `nextreel/web/routes/shared.py`: instantiate new application services for route use.
- Modify `nextreel/web/routes/watched.py`: delegate import and progress work.
- Add `tests/application/test_letterboxd_import_service.py`.
- Add `tests/application/test_watched_progress_service.py`.
- Add focused assertions to existing watched-store tests.

## Task 1: Letterboxd Import Service

**Files:**
- Create: `nextreel/application/letterboxd_import_service.py`
- Test: `tests/application/test_letterboxd_import_service.py`

- [x] **Step 1: Write failing service tests**

```python
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from movies.letterboxd_import import MatchResult
from nextreel.application.letterboxd_import_service import LetterboxdImportService


class UploadedFile:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.stream = io.BytesIO(content)


async def test_missing_upload_returns_select_file_outcome():
    service = LetterboxdImportService()

    result = await service.import_watched(
        user_id="user-1",
        uploaded=None,
        db_pool=AsyncMock(),
        watched_store=AsyncMock(),
        enqueue_fn=AsyncMock(),
    )

    assert result.kind == "missing_file"
    assert result.flash_message == "Please select a CSV file."


async def test_oversized_upload_returns_file_too_large_outcome():
    service = LetterboxdImportService(max_upload_bytes=3)
    uploaded = UploadedFile("watched.csv", b"abcd")

    result = await service.import_watched(
        user_id="user-1",
        uploaded=uploaded,
        db_pool=AsyncMock(),
        watched_store=AsyncMock(),
        enqueue_fn=AsyncMock(),
    )

    assert result.kind == "file_too_large"
    assert result.flash_message == "File is too large. Maximum size is 5MB."


async def test_success_adds_matches_and_requests_enrichment():
    matcher = AsyncMock(return_value=MatchResult(matched=["tt1", "tt2"], unmatched=[], total=2))
    scheduler = AsyncMock()
    watched_store = SimpleNamespace(add_bulk=AsyncMock(return_value=2))
    service = LetterboxdImportService(match_films_fn=matcher, schedule_enrichment_fn=scheduler)
    uploaded = UploadedFile(
        "watched.csv",
        b"Date,Name,Year,Letterboxd URI\n2021-01-01,Inception,2010,x\n",
    )

    result = await service.import_watched(
        user_id="user-1",
        uploaded=uploaded,
        db_pool=AsyncMock(),
        watched_store=watched_store,
        enqueue_fn=AsyncMock(),
    )

    assert result.kind == "success"
    assert result.matched == ["tt1", "tt2"]
    assert result.flash_message == "Imported all 2 films."
    watched_store.add_bulk.assert_awaited_once_with("user-1", ["tt1", "tt2"])
    scheduler.assert_awaited_once()


async def test_partial_match_preserves_unmatched_labels():
    matcher = AsyncMock(
        return_value=MatchResult(
            matched=["tt1"],
            unmatched=[{"name": "Unknown Film", "year": 2050}],
            total=2,
        )
    )
    watched_store = SimpleNamespace(add_bulk=AsyncMock(return_value=1))
    service = LetterboxdImportService(match_films_fn=matcher, schedule_enrichment_fn=AsyncMock())
    uploaded = UploadedFile(
        "watched.csv",
        b"Date,Name,Year,Letterboxd URI\n2021-01-01,Inception,2010,x\n",
    )

    result = await service.import_watched(
        user_id="user-1",
        uploaded=uploaded,
        db_pool=AsyncMock(),
        watched_store=watched_store,
        enqueue_fn=AsyncMock(),
    )

    assert result.kind == "success"
    assert result.flash_message == "Imported 1 films. 1 could not be matched."
    assert result.unmatched_labels == ["Unknown Film (2050)"]
```

- [x] **Step 2: Run the tests to verify failure**

Run: `pytest tests/application/test_letterboxd_import_service.py -v`

Expected: fails because `nextreel.application.letterboxd_import_service` does not exist.

- [x] **Step 3: Implement the service**

Implement a dataclass outcome with `kind`, `flash_message`, `flash_category`, `matched`, and `unmatched_labels`. `LetterboxdImportService.import_watched()` reads the upload stream, enforces `max_upload_bytes`, parses CSV, delegates matching, bulk-adds matches, schedules enrichment when `enqueue_fn` exists and matches are present, and returns route-ready outcome fields.

- [x] **Step 4: Run the service tests**

Run: `pytest tests/application/test_letterboxd_import_service.py -v`

Expected: all tests in that file pass.

## Task 2: Watched Progress Service

**Files:**
- Create: `nextreel/application/watched_progress_service.py`
- Modify: `movies/watched_store.py`
- Modify: `nextreel/web/route_services.py`
- Test: `tests/application/test_watched_progress_service.py`
- Test: `tests/movies/test_watched_store.py`

- [x] **Step 1: Write failing progress tests**

```python
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


async def test_newly_ready_movies_are_returned_and_marked_sent():
    watched_store = SimpleNamespace(
        ready_tconsts_for_import=AsyncMock(return_value={"tt2"}),
        ready_import_rows=AsyncMock(return_value=[{"tconst": "tt2", "primaryTitle": "Ready Movie"}]),
    )
    session_state = {
        "letterboxd_import_tconsts": ["tt1", "tt2"],
        "letterboxd_sent_tconsts": ["tt1"],
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
```

- [x] **Step 2: Write failing store test**

```python
async def test_ready_import_rows_queries_ready_projection_rows(mock_db_pool):
    mock_db_pool.execute.return_value = [{"tconst": "tt1", "primaryTitle": "Inception"}]
    store = WatchedStore(mock_db_pool)

    rows = await store.ready_import_rows("user-1", ["tt1"])

    assert rows == [{"tconst": "tt1", "primaryTitle": "Inception"}]
    query, params = mock_db_pool.execute.call_args[0][0], mock_db_pool.execute.call_args[0][1]
    assert "INNER JOIN movie_projection" in query
    assert "projection_state = %s" in query
    assert params == ["tt1", "user-1", "ready"]
```

- [x] **Step 3: Run the tests to verify failure**

Run: `pytest tests/application/test_watched_progress_service.py tests/movies/test_watched_store.py::test_ready_import_rows_queries_ready_projection_rows -v`

Expected: fails because the progress service and store method do not exist.

- [x] **Step 4: Implement progress service and store methods**

Implement `WatchedEnrichmentProgressService.progress()` to read `letterboxd_import_tconsts` and `letterboxd_sent_tconsts`, ask `watched_store.ready_tconsts_for_import()` for ready IDs, ask `watched_store.ready_import_rows()` for full rows, call `presenter.normalize_movie()` for each row, update session sent state, and clear session keys when done.

Add `WatchedStore.ready_tconsts_for_import(tconsts)` and `WatchedStore.ready_import_rows(user_id, tconsts)` using parameterized SQL.

Add `WatchedListPresenter.normalize_movie(row, now)` as a public wrapper around the existing private normalization logic.

- [x] **Step 5: Run the targeted tests**

Run: `pytest tests/application/test_watched_progress_service.py tests/movies/test_watched_store.py::test_ready_import_rows_queries_ready_projection_rows -v`

Expected: tests pass.

## Task 3: Route Delegation

**Files:**
- Modify: `nextreel/web/routes/shared.py`
- Modify: `nextreel/web/routes/watched.py`
- Test: `tests/web/test_watched_routes.py`

- [x] **Step 1: Write route characterization tests**

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quart import g

import routes


def _state():
    return SimpleNamespace(session_id="session-1", csrf_token="test-csrf-token", user_id="user-1", filters={})


@pytest.mark.asyncio
async def test_import_letterboxd_route_delegates_to_service(app):
    service_result = SimpleNamespace(
        kind="success",
        flash_message="Imported all 1 films.",
        flash_category="success",
        matched=["tt1"],
        unmatched_labels=[],
        enrichment_requested=True,
    )
    with app.test_request_context(
        "/watched/import-letterboxd",
        method="POST",
        data={"letterboxd_csv": (b"Date,Name,Year\n2020-01-01,Inception,2010\n", "watched.csv")},
        headers={"X-CSRFToken": "test-csrf-token"},
    ):
        g.navigation_state = _state()
        routes._letterboxd_import_service.import_watched = AsyncMock(return_value=service_result)

        response = await routes.import_letterboxd()

    assert response.status_code == 302
    routes._letterboxd_import_service.import_watched.assert_awaited_once()


@pytest.mark.asyncio
async def test_enrichment_progress_route_returns_service_json(app):
    service_result = SimpleNamespace(
        new_movies=[],
        new_count=0,
        total_ready=1,
        total=2,
        done=False,
    )
    with app.test_request_context("/watched/enrichment-progress"):
        g.navigation_state = _state()
        routes._watched_progress_service.progress = AsyncMock(return_value=service_result)

        response = await routes.enrichment_progress()
        payload = await response.get_json()

    assert payload == {"html": "", "new_count": 0, "total_ready": 1, "total": 2, "done": False}
    routes._watched_progress_service.progress.assert_awaited_once()
```

- [x] **Step 2: Run selected route tests**

Run: `pytest tests/web/test_auth_routes.py tests/web/test_routes_extended.py tests/web/test_app.py -q`

Expected: existing route tests pass before refactor; new route characterization tests fail until shared services are exposed and the handlers delegate.

- [x] **Step 3: Refactor watched routes**

Update `import_letterboxd()` to call `_letterboxd_import_service.import_watched(...)`, store `letterboxd_import_tconsts`, `letterboxd_enrichment_pending`, `letterboxd_sent_tconsts`, and `letterboxd_unmatched` based on the outcome, then flash and redirect.

Update `enrichment_progress()` to call `_watched_progress_service.progress(...)`, render `_watched_card.html` for each returned movie, and return the existing JSON keys: `html`, `new_count`, `total_ready`, `total`, and `done`.

- [x] **Step 4: Run route tests**

Run: `pytest tests/web/test_auth_routes.py tests/web/test_routes_extended.py tests/web/test_app.py -q`

Expected: tests pass.

## Task 4: Verification

**Files:**
- All files touched in Tasks 1-3.

- [x] **Step 1: Run Phase 1 targeted suite**

Run: `pytest tests/application/test_letterboxd_import_service.py tests/application/test_watched_progress_service.py tests/movies/test_letterboxd_import.py tests/movies/test_watched_store.py tests/web/test_auth_routes.py tests/web/test_routes_extended.py tests/web/test_app.py -q`

Expected: all selected tests pass.

- [x] **Step 2: Run full Python suite**

Run: `pytest`

Expected: full suite passes.

- [x] **Step 3: Inspect diff for unrelated churn**

Run: `git diff --stat && git diff --check`

Expected: only Phase 1 files changed; `git diff --check` reports no whitespace errors.
