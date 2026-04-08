"""Shared time utilities for consistent datetime handling.

MySQL DATETIME columns are timezone-naive, so all UTC timestamps stored in
the database must have their timezone info stripped.  Use ``utcnow()`` instead
of ``datetime.utcnow()`` (deprecated in Python 3.12) or bare
``datetime.now()`` (which returns local time).
"""

import os
from datetime import datetime, timezone

_TRUTHY = frozenset({"true", "1", "yes", "on"})
_FALSY = frozenset({"false", "0", "no", "off"})


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (for MySQL compat)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
