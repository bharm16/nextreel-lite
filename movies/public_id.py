"""Public movie identifier — opaque 6-char alphanumeric ID exposed in URLs.

The internal primary key (``tconst``) remains the IMDb identifier in storage.
This module owns the generation, validation, and resolution of the
public-facing alias used in URLs like ``/movie/the-departed-2006-a8fk3j``.
"""

from __future__ import annotations

import re
import secrets

_ID_LENGTH = 6
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 chars
_ID_RE = re.compile(r"^[a-z0-9]{6}$")
_MAX_GENERATION_ATTEMPTS = 8


class PublicIdGenerationError(Exception):
    """Raised when a unique public_id cannot be assigned after retries."""


def generate() -> str:
    """Return a fresh random public_id using a CSPRNG.

    No collision check — callers must handle the rare clash on insert.
    """
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


__all__ = [
    "PublicIdGenerationError",
    "_ID_ALPHABET",
    "_ID_LENGTH",
    "_ID_RE",
    "_MAX_GENERATION_ATTEMPTS",
    "generate",
]
