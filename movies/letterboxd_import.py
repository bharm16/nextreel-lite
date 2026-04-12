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
