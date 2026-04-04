"""HTTP form data parsing for movie filter criteria.

Extracted from ``movies.query_builder`` — this module changes when the
filter UI changes, while query_builder changes when the database schema
changes.
"""

import re

from logging_config import get_logger

logger = get_logger(__name__)

# ── Allow-lists ───────────────────────────────────────────────────

# Valid IMDb genre names — used as an allow-list for genre filtering.
VALID_GENRES = frozenset({
    "Action", "Adventure", "Animation", "Biography", "Comedy", "Crime",
    "Documentary", "Drama", "Family", "Fantasy", "Film-Noir", "History",
    "Horror", "Music", "Musical", "Mystery", "News", "Romance",
    "Sci-Fi", "Short", "Sport", "Thriller", "War", "Western",
})

# Regex for valid ISO 639-1 language codes (2-3 lowercase letters) plus "any".
_LANG_RE = re.compile(r"^[a-z]{2,3}$")


def _safe_int(
    value: str | None,
    default: int | None = None,
    *,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int | None:
    """Parse an integer from form input, returning *default* on failure."""
    if not value:
        return default
    try:
        n = int(value)
    except (ValueError, TypeError):
        return default
    if min_val is not None:
        n = max(n, min_val)
    if max_val is not None:
        n = min(n, max_val)
    return n


def _safe_float(
    value: str | None,
    default: float | None = None,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float | None:
    """Parse a float from form input, returning *default* on failure."""
    if not value:
        return default
    try:
        f = float(value)
    except (ValueError, TypeError):
        return default
    if min_val is not None:
        f = max(f, min_val)
    if max_val is not None:
        f = min(f, max_val)
    return f


def extract_movie_filter_criteria(form_data):
    """Extract and validate filter criteria from form data.

    Returns:
        dict: Dictionary containing the validated filter criteria.
    """
    criteria = {}

    # Year range — clamp to sensible bounds
    min_year = _safe_int(form_data.get('year_min'), min_val=1888, max_val=2100)
    max_year = _safe_int(form_data.get('year_max'), min_val=1888, max_val=2100)
    if min_year is not None:
        criteria['min_year'] = min_year
    if max_year is not None:
        criteria['max_year'] = max_year

    # IMDb score — 0.0 to 10.0
    min_rating = _safe_float(form_data.get('imdb_score_min'), min_val=0.0, max_val=10.0)
    max_rating = _safe_float(form_data.get('imdb_score_max'), min_val=0.0, max_val=10.0)
    if min_rating is not None:
        criteria['min_rating'] = min_rating
    if max_rating is not None:
        criteria['max_rating'] = max_rating

    # Vote counts — non-negative
    min_votes = _safe_int(form_data.get('num_votes_min'), min_val=0)
    max_votes = _safe_int(form_data.get('num_votes_max'), min_val=0)
    if min_votes is not None:
        criteria['min_votes'] = min_votes
    if max_votes is not None:
        criteria['max_votes'] = max_votes

    # Genres — validate against allow-list to prevent LIKE wildcard injection
    raw_genres = form_data.getlist('genres[]')
    genres = [g for g in raw_genres if g and isinstance(g, str) and g in VALID_GENRES]
    if genres:
        criteria['genres'] = genres

    # Language — validate against ISO 639-1 pattern or "any"
    language = form_data.get('language', 'en')
    if not isinstance(language, str) or (language != "any" and not _LANG_RE.match(language)):
        language = 'en'
    criteria['language'] = language
    logger.debug("Language filter set to: %s", language)

    return criteria
