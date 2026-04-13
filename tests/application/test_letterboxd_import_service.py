from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


async def test_invalid_csv_returns_invalid_csv_outcome():
    service = LetterboxdImportService()
    uploaded = UploadedFile("watched.csv", b"Date,Title,Year\n2020-01-01,Inception,2010\n")

    result = await service.import_watched(
        user_id="user-1",
        uploaded=uploaded,
        db_pool=AsyncMock(),
        watched_store=AsyncMock(),
        enqueue_fn=AsyncMock(),
    )

    assert result.kind == "invalid_csv"
    assert result.flash_category == "error"
    assert result.flash_message.startswith("Invalid CSV format: Missing required column: Name.")


async def test_empty_csv_returns_no_films_outcome():
    service = LetterboxdImportService()
    uploaded = UploadedFile("watched.csv", b"Date,Name,Year\n")

    result = await service.import_watched(
        user_id="user-1",
        uploaded=uploaded,
        db_pool=AsyncMock(),
        watched_store=AsyncMock(),
        enqueue_fn=AsyncMock(),
    )

    assert result.kind == "no_films"
    assert result.flash_category == "warning"
    assert result.flash_message == "The CSV file contained no films."


async def test_success_adds_matches_and_requests_enrichment():
    matcher = AsyncMock(return_value=MatchResult(matched=["tt1", "tt2"], unmatched=[], total=2))
    scheduler = AsyncMock()
    watched_store = SimpleNamespace(add_bulk=AsyncMock(return_value=2))
    service = LetterboxdImportService(match_films_fn=matcher, schedule_enrichment_fn=scheduler)
    uploaded = UploadedFile(
        "watched.csv",
        b"Date,Name,Year,Letterboxd URI\n2021-01-01,Inception,2010,x\n",
    )
    enqueue_fn = AsyncMock()

    result = await service.import_watched(
        user_id="user-1",
        uploaded=uploaded,
        db_pool=AsyncMock(),
        watched_store=watched_store,
        enqueue_fn=enqueue_fn,
    )

    assert result.kind == "success"
    assert result.matched == ["tt1", "tt2"]
    assert result.enrichment_requested is True
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
