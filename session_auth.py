import hashlib
import logging
import os
import secrets
import time
import uuid

from quart import request, session

logger = logging.getLogger(__name__)

SESSION_TOKEN_KEY = "session_token"
SESSION_FINGERPRINT_KEY = "session_fp"

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

    if "session_token" not in session:
        session["session_token"] = generate_session_token()
        session["user_id"] = str(uuid.uuid4())
        session["created_at"] = time.time()
        logger.info("Created new session for user: %s", session["user_id"])

        await movie_manager.add_user(session["user_id"], DEFAULT_CRITERIA)

        user_sessions_total.inc()
        if metrics_collector:
            metrics_collector.track_user_activity(session["user_id"])

    # Check session age
    if "created_at" in session:
        session_age = time.time() - session["created_at"]
        if session_age > SESSION_MAX_AGE:
            session.clear()
            session["session_token"] = generate_session_token()
            session["user_id"] = str(uuid.uuid4())
            session["created_at"] = time.time()
            logger.info("Session expired, created new session")

    # Ensure user is initialised in movie manager
    user_id = session.get("user_id")
    if user_id and "initialized" not in session:
        criteria = session.get("criteria", DEFAULT_CRITERIA)
        await movie_manager.add_user(user_id, criteria)
        session["initialized"] = True
        logger.info("Initialized user %s in movie manager", user_id)
