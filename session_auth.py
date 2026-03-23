"""Session initialization — registers users with MovieManager.

Token creation, fingerprinting, rotation, and cookie security are all handled
by ``session_security_enhanced.EnhancedSessionSecurity``.  This module is
responsible only for ensuring the user is registered in the movie manager
and that the session contains the required navigation state.
"""

import time
import uuid
from datetime import datetime

from quart import session

from session_keys import (
    USER_ID_KEY,
    CREATED_AT_KEY,
    INITIALIZED_KEY,
    CRITERIA_KEY,
)
from logging_config import get_logger

logger = get_logger(__name__)

def _default_criteria() -> dict:
    """Return a fresh copy of default criteria.

    Built each call so ``max_year`` stays current and the mutable ``genres``
    list is never shared between sessions.
    """
    return {
        "min_year": 1900,
        "max_year": datetime.now().year,
        "min_rating": 7.0,
        "genres": ["Action", "Comedy"],
    }

SESSION_MAX_AGE = 24 * 60 * 60  # 24 hours


async def init_session(movie_manager, metrics_collector=None):
    """Ensure the user is registered in the movie manager.

    Token and fingerprint are created by ``EnhancedSessionSecurity`` in its
    own ``before_request`` handler.  This function only handles user
    registration and session-age expiry.
    """
    from metrics_collector import user_sessions_total

    is_new = False

    # Ensure a user_id is always present
    if USER_ID_KEY not in session:
        session[USER_ID_KEY] = str(uuid.uuid4())
        session[CREATED_AT_KEY] = time.time()
        is_new = True

    if is_new:
        logger.info("Created new session for user: %s", session[USER_ID_KEY])
        await movie_manager.add_user(session[USER_ID_KEY], _default_criteria())
        session[INITIALIZED_KEY] = True
        user_sessions_total.inc()
        if metrics_collector:
            metrics_collector.track_user_activity(session[USER_ID_KEY])

    # Check session age
    if CREATED_AT_KEY in session:
        session_age = time.time() - session[CREATED_AT_KEY]
        if session_age > SESSION_MAX_AGE:
            session.clear()
            session[USER_ID_KEY] = str(uuid.uuid4())
            session[CREATED_AT_KEY] = time.time()
            logger.info("Session expired, created new session")

    # Ensure user is initialised in movie manager
    user_id = session.get(USER_ID_KEY)
    if user_id and INITIALIZED_KEY not in session:
        criteria = session.get(CRITERIA_KEY, _default_criteria())
        await movie_manager.add_user(user_id, criteria)
        session[INITIALIZED_KEY] = True
        logger.info("Initialized user %s in movie manager", user_id)
