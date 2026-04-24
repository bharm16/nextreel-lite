"""Centralized session key constants.

Every session key used by the application is defined here.  All modules
that read or write session state should import keys from this module
rather than using raw string literals.
"""

# ── Movie navigation state ─────────────────────────────────────────
CRITERIA_KEY = "criteria"
WATCH_QUEUE_KEY = "watch_queue"
PREVIOUS_STACK_KEY = "previous_movies_stack"
FUTURE_STACK_KEY = "future_movies_stack"
CURRENT_MOVIE_KEY = "current_movie"
SEEN_TCONSTS_KEY = "seen_tconsts"
CURRENT_FILTERS_KEY = "current_filters"

# ── OAuth ──────────────────────────────────────────────────────────
SESSION_OAUTH_STATE_KEY = "oauth_state"
SESSION_OAUTH_NEXT_KEY = "oauth_next"
