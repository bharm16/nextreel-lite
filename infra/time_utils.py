"""Shared time utilities for consistent datetime handling.

MySQL DATETIME columns are timezone-naive, so all UTC timestamps stored in
the database must have their timezone info stripped.  Use ``utcnow()`` instead
of ``datetime.utcnow()`` (deprecated in Python 3.12) or bare
``datetime.now()`` (which returns local time).
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (for MySQL compat)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
