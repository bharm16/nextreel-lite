"""Centralized session key constants and helper functions.

Every session key used by the application is defined here.  All modules
that read or write session state should import keys from this module
rather than using raw string literals.
"""

from quart import session

# ── Identity & auth ────────────────────────────────────────────────
SESSION_TOKEN_KEY = "session_token"
SESSION_FINGERPRINT_KEY = "session_fp"
SESSION_CREATED_KEY = "session_created"
SESSION_LAST_ACTIVITY_KEY = "session_last_activity"
SESSION_ROTATION_COUNT_KEY = "session_rotation_count"
USER_ID_KEY = "user_id"
CREATED_AT_KEY = "created_at"
INITIALIZED_KEY = "initialized"

# ── Movie navigation state ─────────────────────────────────────────
CRITERIA_KEY = "criteria"
WATCH_QUEUE_KEY = "watch_queue"
PREVIOUS_STACK_KEY = "previous_movies_stack"
FUTURE_STACK_KEY = "future_movies_stack"
CURRENT_MOVIE_KEY = "current_movie"
SEEN_TCONSTS_KEY = "seen_tconsts"
QUEUE_SIZE_KEY = "queue_size"
CURRENT_FILTERS_KEY = "current_filters"

# ── Fingerprint (removed) ──────────────────────────────────────────
# FINGERPRINT_COMPONENTS_KEY removed — fingerprint components are
# recomputed from live headers, not stored in the session.


def reset_movie_stacks():
    """Clear all movie navigation state from the session."""
    session[WATCH_QUEUE_KEY] = []
    session[PREVIOUS_STACK_KEY] = []
    session[FUTURE_STACK_KEY] = []
    session[SEEN_TCONSTS_KEY] = []
    session.pop(CURRENT_MOVIE_KEY, None)


def init_movie_stacks(criteria):
    """Initialise movie navigation state for a new user."""
    session.setdefault(CRITERIA_KEY, criteria)
    session.setdefault(WATCH_QUEUE_KEY, [])
    session.setdefault(PREVIOUS_STACK_KEY, [])
    session.setdefault(FUTURE_STACK_KEY, [])
    session.setdefault(SEEN_TCONSTS_KEY, [])
