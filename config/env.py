"""Centralized environment detection.

Every module that needs to know the current environment should import
``get_environment()`` from here rather than repeating the
``os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production"))`` pattern.
"""

import os

_ENV: str | None = None


def get_environment() -> str:
    """Return the current environment name (cached after first call)."""
    global _ENV
    if _ENV is None:
        _ENV = os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production"))
    return _ENV


def _reset_environment() -> None:
    """Clear the cached environment value (for testing only)."""
    global _ENV
    _ENV = None
