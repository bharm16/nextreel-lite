"""URL query-param ↔ internal-criteria translation for the landing-page filter strip.

The landing strip exposes a narrow, hardcoded set of pills (Drama · Comedy · 1990s ·
< 120 min · 7+ rating). This module translates between the URL schema those pills
produce and two internal representations:

  - ``criteria`` — the dict shape consumed by ``fetch_random_landing_film`` and
    ultimately by ``movies.query_builder.MovieQueryBuilder``.
  - ``active_filters`` — the form-schema keyed for ``/filtered_movie``'s actual
    parser (``infra.filter_normalizer.normalize_filters``). The template uses this
    dict to populate hidden inputs in the primary CTA's POST form when filters are
    active (the CTA submits to ``/filtered_movie``).

Invalid values are silently dropped so a malformed shared link still renders
*something*. The set of valid values is intentionally narrow — the full filter
UI on the movie detail page handles the long tail.
"""

from __future__ import annotations

from typing import Any, Mapping

from movies.filter_parser import VALID_GENRES

# ── URL-schema allowlists ─────────────────────────────────────────

_VALID_DECADES: dict[str, tuple[int, int]] = {
    "1970s": (1970, 1979),
    "1980s": (1980, 1989),
    "1990s": (1990, 1999),
    "2000s": (2000, 2009),
    "2010s": (2010, 2019),
    "2020s": (2020, 2029),
}

_VALID_RUNTIMES: dict[str, tuple[str, int]] = {
    "lt90": ("max_runtime", 90),
    "lt120": ("max_runtime", 120),
    "gt150": ("min_runtime", 150),
}

_VALID_RATINGS: dict[str, float] = {
    "6plus": 6.0,
    "7plus": 7.0,
    "8plus": 8.0,
}


def criteria_from_query_args(args: Mapping[str, str]) -> dict[str, Any]:
    """Translate a URL-arg mapping into the internal ``criteria`` dict.

    Unknown params are ignored. Invalid values for known params are silently
    dropped (the URL is treated as if the bad param weren't present).
    """
    criteria: dict[str, Any] = {}

    genre = args.get("genre")
    if isinstance(genre, str) and genre in VALID_GENRES:
        criteria["genres"] = [genre]

    decade = args.get("decade")
    if isinstance(decade, str) and decade in _VALID_DECADES:
        min_year, max_year = _VALID_DECADES[decade]
        criteria["min_year"] = min_year
        criteria["max_year"] = max_year

    runtime = args.get("runtime")
    if isinstance(runtime, str) and runtime in _VALID_RUNTIMES:
        key, value = _VALID_RUNTIMES[runtime]
        criteria[key] = value

    rating = args.get("rating")
    if isinstance(rating, str) and rating in _VALID_RATINGS:
        criteria["min_rating"] = _VALID_RATINGS[rating]

    return criteria


def active_filters_for_template(criteria: Mapping[str, Any]) -> dict[str, str]:
    """Translate ``criteria`` back to the form-schema keys ``/filtered_movie`` expects.

    Keys are the actual HTTP form-field names read by
    ``infra.filter_normalizer.normalize_filters``:

      criteria key   → form key
      ─────────────────────────────────────────────
      genres[0]      → genres[]   (only first genre; normalize_filters uses getlist)
      min_year       → year_min
      max_year       → year_max
      min_rating     → imdb_score_min

    Runtime is dropped because ``/filtered_movie`` (via ``normalize_filters``) has
    no runtime form key; the in-place reroll via ``/api/landing-film`` respects
    runtime, only the Pick-Another form-post path loses runtime context.

    All values are stringified for direct ``<input value="...">`` use.
    """
    active: dict[str, str] = {}

    genres = criteria.get("genres")
    if isinstance(genres, list) and genres:
        # Only first genre — the landing strip is single-genre; normalize_filters
        # reads getlist("genres[]") so a single hidden input works correctly.
        active["genres[]"] = str(genres[0])

    if "min_year" in criteria:
        active["year_min"] = str(criteria["min_year"])
    if "max_year" in criteria:
        active["year_max"] = str(criteria["max_year"])

    if "min_rating" in criteria:
        active["imdb_score_min"] = str(criteria["min_rating"])

    # max_runtime / min_runtime: not emitted — no matching key in normalize_filters.

    return active


__all__ = ["criteria_from_query_args", "active_filters_for_template"]
