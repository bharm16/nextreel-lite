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

    Two-pass strategy so the index on ``primaryTitle`` is actually usable:

    1. **Exact pass** — bulk SELECT ``WHERE primaryTitle IN (...) AND
       startYear IN (...)`` using the prefix index. Handles 90%+ of
       Letterboxd rows, which already carry the movie's canonical title.
    2. **Normalized fallback** — for the remainder, SELECT rows whose
       ``startYear`` matches any unmatched year, then compare normalized
       titles in Python. This only runs on a small residual, so the
       per-year index scan is cheap.

    The old implementation wrapped every row's ``primaryTitle`` in
    ``LOWER(REPLACE(REPLACE(...)))`` inside a massive OR list, which
    forced a full-table scan per batch. For an import of ~2,500 films
    this moved from ~25s to well under 1s in practice.

    Args:
        db_pool: database connection pool with ``execute()`` method.
        films: list of ``{"name": str, "year": int}`` dicts.

    Returns:
        MatchResult with matched tconsts and unmatched film dicts.
    """
    result = MatchResult(total=len(films))
    if not films:
        return result

    # Pending map keyed by (normalized_title, year) for the fallback pass
    # and fast removal as we consume matches.
    pending: dict[tuple[str, int], dict] = {}
    for f in films:
        key = (normalize_title(f["name"]), f["year"])
        pending[key] = f

    # Pass 1: exact-title + year bulk SELECT, batched.
    for i in range(0, len(films), _MATCH_BATCH_SIZE):
        batch = films[i : i + _MATCH_BATCH_SIZE]
        title_values = list({f["name"] for f in batch})
        year_values = list({f["year"] for f in batch})
        if not title_values or not year_values:
            continue

        title_placeholders = ",".join(["%s"] * len(title_values))
        year_placeholders = ",".join(["%s"] * len(year_values))
        query = (
            "SELECT tconst, primaryTitle, startYear "
            "FROM movie_candidates "
            f"WHERE primaryTitle IN ({title_placeholders}) "
            f"AND startYear IN ({year_placeholders})"
        )
        rows = await db_pool.execute(query, [*title_values, *year_values], fetch="all")
        if not rows:
            continue

        for row in rows:
            key = (normalize_title(row["primaryTitle"]), row["startYear"])
            if key in pending:
                result.matched.append(row["tconst"])
                del pending[key]

    if not pending:
        result.unmatched = []
        return result

    # Pass 2: normalized fallback for the residual. Fetch all candidates
    # that share a startYear with any unmatched film, then compare the
    # normalized titles in Python. Bounded by the number of distinct
    # unmatched years, not by the size of the input list.
    unmatched_years = sorted({year for _title, year in pending})
    year_placeholders = ",".join(["%s"] * len(unmatched_years))
    fallback_query = (
        "SELECT tconst, primaryTitle, startYear "
        "FROM movie_candidates "
        f"WHERE startYear IN ({year_placeholders})"
    )
    fallback_rows = await db_pool.execute(
        fallback_query, list(unmatched_years), fetch="all"
    )
    if fallback_rows:
        for row in fallback_rows:
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
