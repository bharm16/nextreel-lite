"""Bulk session revocation for a given user.

Used by:
- Password change success (revoke all except current)
- Explicit "Sign out everywhere" button (revoke all except current)
- Account deletion (revoke all including current)
"""

from __future__ import annotations

import json

from config.session import SessionConfig
from logging_config import get_logger

logger = get_logger(__name__)

_SESSION_KEY_PREFIX = SessionConfig.SESSION_KEY_PREFIX.encode()
_SESSION_KEY_PATTERN = _SESSION_KEY_PREFIX + b"*"


async def revoke_user_sessions(
    redis_client,
    user_id: str,
    *,
    except_session_id: str | None = None,
) -> int:
    """Delete every quart-session entry whose stored user_id matches.

    Returns the number of sessions revoked. Silently tolerates malformed
    session payloads (treated as non-matches so a poison entry cannot
    block revocation for well-formed ones).
    """
    cursor: int = 0
    revoked = 0
    except_suffix = _SESSION_KEY_PREFIX + except_session_id.encode() if except_session_id else None

    while True:
        cursor, keys = await redis_client.scan(cursor=cursor, match=_SESSION_KEY_PATTERN, count=500)
        for key in keys:
            if except_suffix is not None and key == except_suffix:
                continue
            payload = await redis_client.get(key)
            if payload is None:
                continue
            try:
                data = json.loads(payload)
            except (ValueError, TypeError):
                logger.debug("Skipping unparseable session key %r", key)
                continue
            if isinstance(data, dict) and data.get("user_id") == user_id:
                await redis_client.delete(key)
                revoked += 1
        if cursor == 0:
            break

    logger.info("Revoked %d sessions for user=%s", revoked, user_id)
    return revoked
