"""Letterboxd CSV import: parsing, normalization, and DB matching."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field


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
    text = io.TextIOWrapper(file_stream, encoding="utf-8")
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
