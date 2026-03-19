import hashlib
import logging
import os
import secrets
import time
import uuid

from quart import request, session

from session_keys import (
    SESSION_TOKEN_KEY,
    SESSION_FINGERPRINT_KEY,
    USER_ID_KEY,
    CREATED_AT_KEY,
    INITIALIZED_KEY,
    CRITERIA_KEY,
)

logger = logging.getLogger(__name__)

DEFAULT_CRITERIA = {
    "min_year": 1900,
    "max_year": 2023,
    "min_rating": 7.0,
    "genres": ["Action", "Comedy"],
}
SESSION_MAX_AGE = 24 * 60 * 60  # 24 hours


def generate_session_token() -> str:
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)


def generate_fingerprint(user_agent: str, ip: str) -> str:
    """Generate a fingerprint tied to the user agent and IP."""
    data = f"{user_agent}|{ip}|{os.getenv('FLASK_SECRET_KEY', '')}"
    return hashlib.sha256(data.encode()).hexdigest()


def ensure_session() -> None:
    """Ensure the session contains a token and fingerprint bound to the client."""
    current_fp = generate_fingerprint(
        request.headers.get("User-Agent", ""), request.remote_addr or ""
    )
    token = session.get(SESSION_TOKEN_KEY)
    fp = session.get(SESSION_FINGERPRINT_KEY)

    if not token or fp != current_fp:
        session.clear()
        session[SESSION_TOKEN_KEY] = generate_session_token()
        session[SESSION_FINGERPRINT_KEY] = current_fp


async def init_session(movie_manager, metrics_collector=None):
    """Initialize or refresh the user session.

    Creates a new session if none exists, expires old sessions, and
    ensures the user is registered in the movie manager.  Designed to
    be called from a ``before_request`` handler.
    """
    from metrics_collector import user_sessions_total

    if SESSION_TOKEN_KEY not in session:
        session[SESSION_TOKEN_KEY] = generate_session_token()
        session[USER_ID_KEY] = str(uuid.uuid4())
        session[CREATED_AT_KEY] = time.time()
        logger.info("Created new session for user: %s", session[USER_ID_KEY])

        await movie_manager.add_user(session[USER_ID_KEY], DEFAULT_CRITERIA)

        user_sessions_total.inc()
        if metrics_collector:
            metrics_collector.track_user_activity(session[USER_ID_KEY])

    # Check session age
    if CREATED_AT_KEY in session:
        session_age = time.time() - session[CREATED_AT_KEY]
        if session_age > SESSION_MAX_AGE:
            session.clear()
            session[SESSION_TOKEN_KEY] = generate_session_token()
            session[USER_ID_KEY] = str(uuid.uuid4())
            session[CREATED_AT_KEY] = time.time()
            logger.info("Session expired, created new session")

    # Ensure user is initialised in movie manager
    user_id = session.get(USER_ID_KEY)
    if user_id and INITIALIZED_KEY not in session:
        criteria = session.get(CRITERIA_KEY, DEFAULT_CRITERIA)
        await movie_manager.add_user(user_id, criteria)
        session[INITIALIZED_KEY] = True
        logger.info("Initialized user %s in movie manager", user_id)
