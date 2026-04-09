"""Shared time utilities for consistent datetime handling.

MySQL DATETIME columns are timezone-naive, so all UTC timestamps stored in
the database must have their timezone info stripped.  Use ``utcnow()`` instead
of ``datetime.utcnow()`` (deprecated in Python 3.12) or bare
``datetime.now()`` (which returns local time).
"""

import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"true", "1", "yes", "on"})
_FALSY = frozenset({"false", "0", "no", "off"})


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (for MySQL compat)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_CURRENT_YEAR_CACHE: tuple[float, int] | None = None
_CURRENT_YEAR_TTL_SECONDS = 3600.0


def current_year() -> int:
    """Return the current UTC year, cached with a 1-hour TTL.

    The tiny TTL doesn't matter for anything year-granular (filter min/max
    year, current-year labels), but saves a stdlib datetime allocation on
    every hot-path call site.
    """
    global _CURRENT_YEAR_CACHE
    now = time.monotonic()
    if _CURRENT_YEAR_CACHE is not None:
        expires_at, year = _CURRENT_YEAR_CACHE
        if now < expires_at:
            return year
    year = datetime.now(timezone.utc).year
    _CURRENT_YEAR_CACHE = (now + _CURRENT_YEAR_TTL_SECONDS, year)
    return year


def _reset_current_year_cache() -> None:
    """Test-only helper to clear the current_year cache."""
    global _CURRENT_YEAR_CACHE
    _CURRENT_YEAR_CACHE = None


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var with a canonical truthy/falsy set.

    Unknown values fall back to ``default`` rather than silently parsing
    as false — callers get the documented default instead of a surprise.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return default


def env_int(name: str, default: int) -> int:
    """Parse an integer env var. Unknown or invalid values fall back to default."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        logger.warning("Invalid int for %s=%r; using default %d", name, raw, default)
        return default


def env_float(name: str, default: float) -> float:
    """Parse a float env var. Unknown or invalid values fall back to default."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        logger.warning("Invalid float for %s=%r; using default %r", name, raw, default)
        return default
