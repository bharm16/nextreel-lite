"""Letterboxd CSV import: parsing, normalization, and DB matching."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from logging_config import get_logger

logger = get_logger(__name__)

_MATCH_BATCH_SIZE = 200


def normalize_title(title: str) -> str:
    """Normalize a film title for matching.

    Lowercase, replace en/em dashes with hyphens, collapse whitespace.
    """
    t = title.lower()
    t = t.replace("\u2013", "-")  # en-dash
    t = t.replace("\u2014", "-")  # em-dash
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_watched_csv(file_stream: io.BufferedIOBase) -> list[dict]:
    """Parse a Letterboxd watched.csv export.

    Args:
        file_stream: binary file-like object (e.g. from request.files).

    Returns:
        List of ``{"name": str, "year": int}`` dicts.

    Raises:
        ValueError: if required columns (Name, Year) are missing.
    """
    text = io.TextIOWrapper(file_stream, encoding="utf-8-sig")
    reader = csv.DictReader(text)

    if reader.fieldnames is None:
        raise ValueError("Empty CSV file")
    fields = set(reader.fieldnames)
    if "Name" not in fields:
        raise ValueError("Missing required column: Name")
    if "Year" not in fields:
        raise ValueError("Missing required column: Year")

    films = []
    for row in reader:
        name = (row.get("Name") or "").strip()
        year_raw = (row.get("Year") or "").strip()
        if not name or not year_raw:
            continue
        try:
            year = int(year_raw)
        except ValueError:
            continue
        films.append({"name": name, "year": year})

    return films


@dataclass
class MatchResult:
    """Result of matching Letterboxd films against the DB."""

    matched: list[str] = field(default_factory=list)  # tconst values
    unmatched: list[dict] = field(default_factory=list)  # {name, year} dicts
    total: int = 0


async def match_films(db_pool, films: list[dict]) -> MatchResult:
    """Match (name, year) pairs against movie_candidates by normalized title.

    Args:
        db_pool: database connection pool with ``execute()`` method.
        films: list of ``{"name": str, "year": int}`` dicts.

    Returns:
        MatchResult with matched tconsts and unmatched film dicts.
    """
    result = MatchResult(total=len(films))
    if not films:
        return result

    # Build lookup keyed by (normalized_title, year) -> original film dict
    pending = {}
    for f in films:
        key = (normalize_title(f["name"]), f["year"])
        pending[key] = f

    # Query in batches
    for i in range(0, len(films), _MATCH_BATCH_SIZE):
        batch_films = films[i : i + _MATCH_BATCH_SIZE]
        batch_keys = [(normalize_title(f["name"]), f["year"]) for f in batch_films]

        conditions = []
        params = []
        for norm_title, year in batch_keys:
            conditions.append(
                "(LOWER(REPLACE(REPLACE(primaryTitle, '\u2013', '-'), '\u2014', '-')) = %s"
                " AND startYear = %s)"
            )
            params.extend([norm_title, year])

        query = (
            "SELECT tconst, primaryTitle, startYear "
            "FROM movie_candidates "
            "WHERE " + " OR ".join(conditions)
        )

        rows = await db_pool.execute(query, params, fetch="all")
        if not rows:
            continue

        for row in rows:
            key = (normalize_title(row["primaryTitle"]), row["startYear"])
            if key in pending:
                result.matched.append(row["tconst"])
                del pending[key]

    result.unmatched = list(pending.values())
    return result


import asyncio as _asyncio

_ENQUEUE_BATCH_SIZE = 50
_ENQUEUE_BATCH_DELAY = 1.0  # seconds between batches


async def enqueue_import_enrichment(
    tconsts: list[str],
    db_pool,
    enqueue_fn,
) -> None:
    """Batch-enqueue enrichment jobs for imported tconsts lacking READY projections.

    Runs as a fire-and-forget task. Catches all exceptions to avoid crashing.

    Args:
        tconsts: list of tconst strings from the import.
        db_pool: database pool for querying projection state.
        enqueue_fn: async callable to enqueue arq jobs, or None to skip.
    """
    if not tconsts or enqueue_fn is None:
        return

    try:
        # Find which tconsts already have READY projections
        placeholders = ", ".join(["%s"] * len(tconsts))
        ready_rows = await db_pool.execute(
            "SELECT tconst FROM movie_projection "
            "WHERE tconst IN (" + placeholders + ") "
            "AND projection_state = %s",
            [*tconsts, "ready"],
            fetch="all",
        )
        ready_set = {row["tconst"] for row in ready_rows} if ready_rows else set()
        needs_enrichment = [tc for tc in tconsts if tc not in ready_set]

        if not needs_enrichment:
            logger.info("All %d imported tconsts already READY, skipping enrichment", len(tconsts))
            return

        logger.info(
            "Enqueuing enrichment for %d of %d imported tconsts",
            len(needs_enrichment),
            len(tconsts),
        )

        for i in range(0, len(needs_enrichment), _ENQUEUE_BATCH_SIZE):
            batch = needs_enrichment[i : i + _ENQUEUE_BATCH_SIZE]
            for tc in batch:
                try:
                    await enqueue_fn(
                        "enrich_projection", tc, None,
                        _job_id="enrich:%s" % tc,
                    )
                except Exception:
                    logger.debug("Failed to enqueue enrichment for %s", tc, exc_info=True)

            if i + _ENQUEUE_BATCH_SIZE < len(needs_enrichment):
                await _asyncio.sleep(_ENQUEUE_BATCH_DELAY)

    except Exception:
        logger.exception("enqueue_import_enrichment failed")
