"""Public movie identifier — opaque 6-char alphanumeric ID exposed in URLs.

The internal primary key (``tconst``) remains the IMDb identifier in storage.
This module owns the generation, validation, and resolution of the
public-facing alias used in URLs like ``/movie/the-departed-2006-a8fk3j``.
"""

from __future__ import annotations

import re
import secrets

from pymysql.err import IntegrityError

from infra.errors import DatabaseError
from logging_config import get_logger

logger = get_logger(__name__)

ID_LENGTH = 6
ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 chars
ID_RE = re.compile(r"^[a-z0-9]{6}$")
MAX_GENERATION_ATTEMPTS = 8
_DUP_KEY_ERRNO = 1062


class PublicIdGenerationError(Exception):
    """Raised when a unique public_id cannot be assigned after retries."""


def is_public_id_collision(exc: BaseException) -> bool:
    """Return True iff ``exc`` is (or wraps) a duplicate-key IntegrityError.

    The pool wraps ``pymysql.err.IntegrityError`` as
    ``DatabaseError(...) from exc`` (see ``infra/pool.py`` ``execute``), so
    in production every UNIQUE-key collision arrives as a ``DatabaseError``
    with the original ``IntegrityError`` on ``__cause__``. Tests bypass the
    pool and raise ``IntegrityError`` directly. Both shapes must be detected,
    plus a string-fallback for the rare case where the cause chain was lost.
    """
    if isinstance(exc, IntegrityError) and exc.args and exc.args[0] == _DUP_KEY_ERRNO:
        return True
    if isinstance(exc, DatabaseError):
        cause = exc.__cause__
        if (
            isinstance(cause, IntegrityError)
            and cause.args
            and cause.args[0] == _DUP_KEY_ERRNO
        ):
            return True
        return f"({_DUP_KEY_ERRNO}," in str(exc)
    return False


def generate() -> str:
    """Return a fresh random public_id using a CSPRNG.

    No collision check — callers must handle the rare clash on insert.
    """
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))


async def assign_public_id(pool, tconst: str) -> str | None:
    """Idempotently assign a public_id to a movie_projection row.

    Returns the assigned (or pre-existing) ID, or ``None`` if no row exists
    for ``tconst``. Safe under contention: the ``UPDATE ... WHERE public_id
    IS NULL`` clause guarantees only one writer wins, and a duplicate-key
    collision (1062) on the unique index triggers a retry with a fresh ID.
    """
    existing = await pool.execute(
        "SELECT public_id FROM movie_projection WHERE tconst = %s",
        [tconst],
        fetch="one",
    )
    if existing is None:
        return None
    current = existing.get("public_id") if isinstance(existing, dict) else existing[0]
    if current:
        return current

    last_error: Exception | None = None
    for _ in range(MAX_GENERATION_ATTEMPTS):
        candidate = generate()
        try:
            affected = await pool.execute(
                """
                UPDATE movie_projection
                SET public_id = %s
                WHERE tconst = %s AND public_id IS NULL
                """,
                [candidate, tconst],
                fetch="none",
            )
        except (IntegrityError, DatabaseError) as exc:
            if is_public_id_collision(exc):
                last_error = exc
                continue
            raise
        if affected:
            return candidate
        # Affected = 0: another writer assigned in between our SELECT and
        # UPDATE. Re-read to return the winning value.
        re_read = await pool.execute(
            "SELECT public_id FROM movie_projection WHERE tconst = %s",
            [tconst],
            fetch="one",
        )
        if re_read:
            value = re_read.get("public_id") if isinstance(re_read, dict) else re_read[0]
            if value:
                return value
        # Row vanished mid-flight — log so a sudden burst of these is
        # visible (suggests deletes are racing the assigner) and treat as
        # not-found so the caller can fall back to its 404 path.
        logger.warning(
            "assign_public_id: row vanished for tconst=%s between SELECT and UPDATE",
            tconst,
        )
        return None

    raise PublicIdGenerationError(
        f"Failed to assign public_id for {tconst} after "
        f"{MAX_GENERATION_ATTEMPTS} attempts (last error: {last_error})"
    )


async def resolve_to_tconst(pool, public_id: str) -> str | None:
    """URL-side lookup: ``public_id`` (from path) → ``tconst`` (PK).

    Validates format before hitting the DB so malicious slugs short-circuit
    without a query. Returns ``None`` for both "invalid format" and "not
    found" so callers can map both to a single 404.
    """
    if not isinstance(public_id, str) or not ID_RE.match(public_id):
        return None
    row = await pool.execute(
        "SELECT tconst FROM movie_projection WHERE public_id = %s",
        [public_id],
        fetch="one",
    )
    if not row:
        return None
    return row.get("tconst") if isinstance(row, dict) else row[0]


async def public_id_for_tconst(pool, tconst: str) -> str | None:
    """Reverse lookup: ``tconst`` → ``public_id`` for outbound URL builders."""
    row = await pool.execute(
        "SELECT public_id FROM movie_projection WHERE tconst = %s",
        [tconst],
        fetch="one",
    )
    if not row:
        return None
    value = row.get("public_id") if isinstance(row, dict) else row[0]
    return value or None


__all__ = [
    "ID_ALPHABET",
    "ID_LENGTH",
    "ID_RE",
    "MAX_GENERATION_ATTEMPTS",
    "PublicIdGenerationError",
    "assign_public_id",
    "generate",
    "is_public_id_collision",
    "public_id_for_tconst",
    "resolve_to_tconst",
]
