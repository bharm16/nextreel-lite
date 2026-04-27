"""Pure helpers for building Reddit-style movie URLs.

URL shape: ``/movie/<title-slug>-<6-char-public-id>``. The trailing 6 chars
are the canonical key the route resolves on; the title slug is decorative
and can be regenerated when titles are corrected (a slug-mismatch on
request triggers a 301 to the canonical form, handled in the route).
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_TRAILING_HYPHENS_RE = re.compile(r"-+$")
_LEADING_HYPHENS_RE = re.compile(r"^-+")
_YEAR_RE = re.compile(r"^\d{4}$")
_TITLE_BODY_MAX_CHARS = 80
# COUPLED to ``_slugify_body``: the slug character class here ([a-z0-9]+)
# must match every character that ``_slugify_body`` can emit. If you ever
# extend the slugifier to allow a new character (e.g. underscores, dots),
# update this regex too — otherwise canonical URLs you generated yourself
# will fail to round-trip through ``parse_movie_path``.
_PATH_RE = re.compile(r"^(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<public_id>[a-z0-9]{6})$")


def _slugify_body(title: str | None) -> str:
    if not title:
        return "untitled"
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    lowered = folded.lower()
    collapsed = _NON_ALNUM_RE.sub("-", lowered)
    trimmed = _LEADING_HYPHENS_RE.sub("", collapsed)
    trimmed = _TRAILING_HYPHENS_RE.sub("", trimmed)
    if not trimmed:
        return "untitled"
    if len(trimmed) > _TITLE_BODY_MAX_CHARS:
        trimmed = trimmed[:_TITLE_BODY_MAX_CHARS]
        trimmed = _TRAILING_HYPHENS_RE.sub("", trimmed)
    return trimmed or "untitled"


def _coerce_year(year) -> str | None:
    if year is None:
        return None
    candidate = str(year).strip()
    if _YEAR_RE.match(candidate):
        return candidate
    return None


def title_slug(primary_title: str | None, year) -> str:
    """Return ``the-departed-2006``-style slug for a movie.

    Strips diacritics, lowercases, replaces non-alphanumeric runs with a
    single hyphen, trims hyphens, caps the title body at 80 chars, and
    appends ``-<year>`` when ``year`` is parseable as a 4-digit number.
    """
    body = _slugify_body(primary_title)
    year_str = _coerce_year(year)
    if year_str:
        return f"{body}-{year_str}"
    return body


def build_movie_path(primary_title: str | None, year, public_id: str) -> str:
    """Return the canonical ``/movie/...`` path for a movie."""
    return f"/movie/{title_slug(primary_title, year)}-{public_id}"


def parse_movie_path(slug_with_id: str) -> tuple[str, str] | None:
    """Parse a movie URL slug into ``(slug_prefix, public_id)``.

    Returns ``None`` when the input is malformed (wrong shape, uppercase,
    bad ID length). Used by the ``/movie/<slug_with_id>`` route handler to
    extract the public_id and decide whether to 301 to canonical.
    """
    if not isinstance(slug_with_id, str) or not slug_with_id:
        return None
    match = _PATH_RE.match(slug_with_id)
    if not match:
        return None
    return match.group("slug"), match.group("public_id")


__all__ = ["build_movie_path", "parse_movie_path", "title_slug"]
