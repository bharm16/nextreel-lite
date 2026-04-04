"""Centralized session key constants.

Every session key used by the application is defined here.  All modules
that read or write session state should import keys from this module
rather than using raw string literals.
"""

# ── Identity & auth ────────────────────────────────────────────────
SESSION_TOKEN_KEY = "session_token"
SESSION_FINGERPRINT_KEY = "session_fp"
SESSION_CREATED_KEY = "session_created"
SESSION_LAST_ACTIVITY_KEY = "session_last_activity"
SESSION_ROTATION_COUNT_KEY = "session_rotation_count"

# ── Movie navigation state ─────────────────────────────────────────
CRITERIA_KEY = "criteria"
WATCH_QUEUE_KEY = "watch_queue"
PREVIOUS_STACK_KEY = "previous_movies_stack"
FUTURE_STACK_KEY = "future_movies_stack"
CURRENT_MOVIE_KEY = "current_movie"
SEEN_TCONSTS_KEY = "seen_tconsts"
CURRENT_FILTERS_KEY = "current_filters"

# ── Fingerprint (removed) ──────────────────────────────────────────
# FINGERPRINT_COMPONENTS_KEY removed — fingerprint components are
# recomputed from live headers, not stored in the session.
