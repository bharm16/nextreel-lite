from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from logging_config import get_logger
from movies.letterboxd_import import (
    MatchResult,
    enqueue_import_enrichment,
    match_films,
    parse_watched_csv,
)

logger = get_logger(__name__)

MAX_LETTERBOXD_UPLOAD_BYTES = 5 * 1024 * 1024

MatchFilmsFn = Callable[[Any, list[dict]], Awaitable[MatchResult]]
# Scheduler contract mirrors ``MovieManager.schedule_background``: accepts a
# coroutine, wraps it in a task registered with the app's drain set, returns
# True on success / False if no scheduler is wired (in which case the coro
# must be closed by the scheduler itself).
BackgroundScheduler = Callable[[Awaitable[Any]], bool]
ScheduleEnrichmentFn = Callable[
    [list[str], Any, Any, "BackgroundScheduler | None"],
    Awaitable[bool],
]


async def _schedule_import_enrichment(
    tconsts: list[str],
    db_pool,
    enqueue_fn,
    background_scheduler: BackgroundScheduler | None = None,
) -> bool:
    if not tconsts or enqueue_fn is None:
        return False
    coro = enqueue_import_enrichment(tconsts, db_pool, enqueue_fn)
    if background_scheduler is None:
        try:
            await coro
        except Exception:
            logger.exception("Letterboxd enrichment enqueue failed")
            return False
        return True
    return bool(background_scheduler(coro))


@dataclass(slots=True)
class LetterboxdImportOutcome:
    kind: str
    flash_message: str
    flash_category: str
    matched: list[str] = field(default_factory=list)
    unmatched_labels: list[str] = field(default_factory=list)
    enrichment_requested: bool = False


class LetterboxdImportService:
    def __init__(
        self,
        *,
        max_upload_bytes: int = MAX_LETTERBOXD_UPLOAD_BYTES,
        match_films_fn: MatchFilmsFn = match_films,
        schedule_enrichment_fn: ScheduleEnrichmentFn = _schedule_import_enrichment,
    ) -> None:
        self.max_upload_bytes = max_upload_bytes
        self.match_films_fn = match_films_fn
        self.schedule_enrichment_fn = schedule_enrichment_fn

    async def import_watched(
        self,
        *,
        user_id: str,
        uploaded,
        db_pool,
        watched_store,
        enqueue_fn,
        background_scheduler: BackgroundScheduler | None = None,
    ) -> LetterboxdImportOutcome:
        if not uploaded or not getattr(uploaded, "filename", None):
            return LetterboxdImportOutcome(
                kind="missing_file",
                flash_message="Please select a CSV file.",
                flash_category="error",
            )

        file_bytes = uploaded.stream.read()
        if isinstance(file_bytes, str):
            file_bytes = file_bytes.encode("utf-8")
        if len(file_bytes) > self.max_upload_bytes:
            return LetterboxdImportOutcome(
                kind="file_too_large",
                flash_message="File is too large. Maximum size is 5MB.",
                flash_category="error",
            )

        try:
            films = parse_watched_csv(io.BytesIO(file_bytes))
        except ValueError as exc:
            return LetterboxdImportOutcome(
                kind="invalid_csv",
                flash_message=(
                    "Invalid CSV format: %s. Please upload the watched.csv from your Letterboxd export."
                    % exc
                ),
                flash_category="error",
            )

        if not films:
            return LetterboxdImportOutcome(
                kind="no_films",
                flash_message="The CSV file contained no films.",
                flash_category="warning",
            )

        result = await self.match_films_fn(db_pool, films)
        await watched_store.add_bulk(user_id, result.matched)
        enrichment_requested = await self.schedule_enrichment_fn(
            result.matched,
            db_pool,
            enqueue_fn,
            background_scheduler,
        )

        matched_count = len(result.matched)
        unmatched_count = len(result.unmatched)
        unmatched_labels = [
            "%s (%s)" % (item["name"], item["year"]) for item in result.unmatched[:50]
        ]
        if unmatched_count:
            message = "Imported %d films. %d could not be matched." % (
                matched_count,
                unmatched_count,
            )
        else:
            message = "Imported all %d films." % matched_count

        return LetterboxdImportOutcome(
            kind="success",
            flash_message=message,
            flash_category="success",
            matched=list(result.matched),
            unmatched_labels=unmatched_labels,
            enrichment_requested=bool(enrichment_requested),
        )
