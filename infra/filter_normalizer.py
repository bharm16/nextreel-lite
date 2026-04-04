"""Filter normalization and criteria conversion utilities.

Extracted from navigation_state.py to keep filter parsing logic
separate from state persistence.
"""

from __future__ import annotations

from typing import Any

from filter_contracts import FilterState, MovieCriteria
from infra.time_utils import utcnow
from movies.filter_parser import VALID_GENRES, extract_movie_filter_criteria

MAX_FILTER_VALUE_LEN = 64
_YEAR_MIN = 1888
_YEAR_MAX = 2100
_RATING_MIN = 0.0
_RATING_MAX = 10.0
_VOTES_MIN = 0


def default_filter_state(current_year: int | None = None) -> FilterState:
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


def filters_from_criteria(criteria: MovieCriteria) -> FilterState:
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

    def __init__(self, filters: FilterState):
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


def criteria_from_filters(filters: FilterState | None) -> MovieCriteria:
    """Convert a filter dict into query criteria understood by the query builder."""
    merged = default_filter_state()
    merged.update(filters or {})
    return extract_movie_filter_criteria(_StoredFilterForm(merged))


def normalize_filters(form_data) -> FilterState:
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


def validate_filters(filters: FilterState) -> dict[str, str]:
    """Validate a canonical filter dict without mutating it."""
    errors: dict[str, str] = {}

    year_min = _parse_bounded_int(
        filters.get("year_min"),
        field="year_min",
        errors=errors,
        minimum=_YEAR_MIN,
        maximum=_YEAR_MAX,
        message=f"Enter a year between {_YEAR_MIN} and {_YEAR_MAX}.",
    )
    year_max = _parse_bounded_int(
        filters.get("year_max"),
        field="year_max",
        errors=errors,
        minimum=_YEAR_MIN,
        maximum=_YEAR_MAX,
        message=f"Enter a year between {_YEAR_MIN} and {_YEAR_MAX}.",
    )
    rating_min = _parse_bounded_float(
        filters.get("imdb_score_min"),
        field="imdb_score_min",
        errors=errors,
        minimum=_RATING_MIN,
        maximum=_RATING_MAX,
        message=f"Enter a rating between {_RATING_MIN:.1f} and {_RATING_MAX:.1f}.",
    )
    rating_max = _parse_bounded_float(
        filters.get("imdb_score_max"),
        field="imdb_score_max",
        errors=errors,
        minimum=_RATING_MIN,
        maximum=_RATING_MAX,
        message=f"Enter a rating between {_RATING_MIN:.1f} and {_RATING_MAX:.1f}.",
    )
    votes_min = _parse_bounded_int(
        filters.get("num_votes_min"),
        field="num_votes_min",
        errors=errors,
        minimum=_VOTES_MIN,
        maximum=None,
        message="Enter a non-negative vote count.",
    )
    votes_max = _parse_bounded_int(
        filters.get("num_votes_max"),
        field="num_votes_max",
        errors=errors,
        minimum=_VOTES_MIN,
        maximum=None,
        message="Enter a non-negative vote count.",
    )

    _validate_range_pair(
        year_min,
        year_max,
        min_field="year_min",
        max_field="year_max",
        errors=errors,
        message="Earliest year must be less than or equal to latest year.",
    )
    _validate_range_pair(
        rating_min,
        rating_max,
        min_field="imdb_score_min",
        max_field="imdb_score_max",
        errors=errors,
        message="Minimum score must be less than or equal to maximum score.",
    )
    _validate_range_pair(
        votes_min,
        votes_max,
        min_field="num_votes_min",
        max_field="num_votes_max",
        errors=errors,
        message="Minimum votes must be less than or equal to maximum votes.",
    )
    return errors


def _parse_bounded_int(
    value: Any,
    *,
    field: str,
    errors: dict[str, str],
    minimum: int | None,
    maximum: int | None,
    message: str,
) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors[field] = message
        return None
    if minimum is not None and parsed < minimum:
        errors[field] = message
        return None
    if maximum is not None and parsed > maximum:
        errors[field] = message
        return None
    return parsed


def _parse_bounded_float(
    value: Any,
    *,
    field: str,
    errors: dict[str, str],
    minimum: float | None,
    maximum: float | None,
    message: str,
) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        errors[field] = message
        return None
    if minimum is not None and parsed < minimum:
        errors[field] = message
        return None
    if maximum is not None and parsed > maximum:
        errors[field] = message
        return None
    return parsed


def _validate_range_pair(
    minimum_value: int | float | None,
    maximum_value: int | float | None,
    *,
    min_field: str,
    max_field: str,
    errors: dict[str, str],
    message: str,
) -> None:
    if min_field in errors or max_field in errors:
        return
    if minimum_value is None or maximum_value is None:
        return
    if minimum_value > maximum_value:
        errors[min_field] = message
        errors[max_field] = message
