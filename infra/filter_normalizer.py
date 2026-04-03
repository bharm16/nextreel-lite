"""Filter normalization and criteria conversion utilities.

Extracted from navigation_state.py to keep filter parsing logic
separate from state persistence.
"""

from __future__ import annotations

from typing import Any

from infra.time_utils import utcnow
from movies.filter_parser import VALID_GENRES, extract_movie_filter_criteria

MAX_FILTER_VALUE_LEN = 64


def default_filter_state(current_year: int | None = None) -> dict[str, Any]:
    year = current_year or utcnow().year
    return {
        "year_min": 1900,
        "year_max": year,
        "imdb_score_min": 7.0,
        "imdb_score_max": 10.0,
        "num_votes_min": 100000,
        "num_votes_max": 200000,
        "language": "en",
        "genres_selected": [],
    }


def filters_from_criteria(criteria: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a filter dict from a criteria dict."""
    filters = default_filter_state()
    if "min_year" in criteria:
        filters["year_min"] = criteria["min_year"]
    if "max_year" in criteria:
        filters["year_max"] = criteria["max_year"]
    if "min_rating" in criteria:
        filters["imdb_score_min"] = criteria["min_rating"]
    if "max_rating" in criteria:
        filters["imdb_score_max"] = criteria["max_rating"]
    if "min_votes" in criteria:
        filters["num_votes_min"] = criteria["min_votes"]
    if "max_votes" in criteria:
        filters["num_votes_max"] = criteria["max_votes"]
    if "language" in criteria:
        filters["language"] = criteria["language"]
    if criteria.get("genres"):
        filters["genres_selected"] = list(criteria["genres"])
    return filters


class _StoredFilterForm:
    """Adapter that wraps a stored filter dict to look like form data."""

    def __init__(self, filters: dict[str, Any]):
        self._filters = filters

    def get(self, key: str, default: Any = None) -> Any:
        if key == "genres[]":
            genres = self._filters.get("genres_selected")
            return genres[0] if genres else default
        return self._filters.get(key, default)

    def getlist(self, key: str) -> list[Any]:
        if key == "genres[]":
            return list(self._filters.get("genres_selected", []))
        value = self._filters.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        return [value]


def criteria_from_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Convert a filter dict into query criteria understood by the query builder."""
    merged = default_filter_state()
    merged.update(filters or {})
    return extract_movie_filter_criteria(_StoredFilterForm(merged))


def normalize_filters(form_data) -> dict[str, Any]:
    """Normalize raw form data into a canonical filter dict."""
    filters = default_filter_state()
    scalar_keys = (
        "year_min",
        "year_max",
        "imdb_score_min",
        "imdb_score_max",
        "num_votes_min",
        "num_votes_max",
        "language",
    )
    for key in scalar_keys:
        value = form_data.get(key)
        if isinstance(value, str):
            filters[key] = value[:MAX_FILTER_VALUE_LEN]
        elif value is not None:
            filters[key] = value

    raw_genres = form_data.getlist("genres[]")
    filters["genres_selected"] = [
        genre[:MAX_FILTER_VALUE_LEN]
        for genre in raw_genres
        if isinstance(genre, str) and genre in VALID_GENRES
    ]
    return filters
