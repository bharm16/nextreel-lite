"""Product-analytics event taxonomy.

Single source of truth for every event the application sends to PostHog (or
any other event backend). Adding a new event happens here first; route code
imports the constants below.

Naming convention
-----------------
- Past tense, snake_case: ``signup_completed``, ``movie_swiped``, ``filter_applied``.
- Verb describes what the user did, not what the system did. ``logged_in``
  not ``session_created``.
- No version suffix. If a property's meaning changes, add a new property
  rather than renaming the event — historical data has to keep working.

Why this file exists
--------------------
Event names are part of the public schema for analytics. Once dashboards,
funnels, retention reports, and stakeholder muscle memory reference an
event name, renaming it costs more than the code change suggests — every
historical query and saved report has to migrate. Centralising names in
one module makes it harder to introduce typos or accidental drift.

Property cardinality discipline
-------------------------------
PostHog tolerates higher property cardinality than Prometheus, but the same
"bucket free-form values" rule applies. ``auth_provider`` is closed-set;
exception messages or raw user input never appear as property values.
"""

from __future__ import annotations

from typing import Literal, TypedDict


# ── Event names ──────────────────────────────────────────────────────
# Constants used at call sites. Treat these as the canonical names —
# do not pass raw strings to track_event.
EVENT_SIGNUP_COMPLETED = "signup_completed"
EVENT_SIGNUP_FAILED = "signup_failed"
EVENT_LOGIN_SUCCEEDED = "login_succeeded"
EVENT_LOGIN_FAILED = "login_failed"
EVENT_LOGGED_OUT = "logged_out"
EVENT_OAUTH_FAILED = "oauth_failed"

EVENT_MOVIE_SWIPED = "movie_swiped"
EVENT_FILTER_APPLIED = "filter_applied"

EVENT_WATCHED_ADDED = "watched_added"
EVENT_WATCHED_REMOVED = "watched_removed"
EVENT_WATCHLIST_ADDED = "watchlist_added"
EVENT_WATCHLIST_REMOVED = "watchlist_removed"

EVENT_SEARCH_PERFORMED = "search_performed"


# Closed-set type aliases used inside event property schemas.
AuthProvider = Literal["email", "google"]
SwipeDirection = Literal["next", "previous"]
SignupFailureReason = Literal["validation", "duplicate", "unavailable"]
LoginFailureReason = Literal["invalid_credentials", "unavailable"]
OAuthFailureReason = Literal["state_mismatch", "provider_conflict", "other"]
SearchResultBucket = Literal["0", "1-5", "6-10", "11+"]
FilterDimension = Literal[
    "genres",
    "year",
    "rating",
    "votes",
    "language",
    "exclude_watched",
    "exclude_watchlist",
]


# ── Property schemas (TypedDict, type-only) ───────────────────────────
# These describe the canonical shape of each event's properties dict.
# They are not enforced at runtime — they exist to give the type checker
# something to chew on at call sites and to document the contract.

class SignupCompletedProperties(TypedDict):
    auth_provider: AuthProvider


class SignupFailedProperties(TypedDict):
    reason: SignupFailureReason


class LoginSucceededProperties(TypedDict):
    auth_provider: AuthProvider


class LoginFailedProperties(TypedDict):
    reason: LoginFailureReason


class LoggedOutProperties(TypedDict):
    session_duration_seconds: float


class OAuthFailedProperties(TypedDict):
    provider: Literal["google"]
    reason: OAuthFailureReason


class MovieSwipedProperties(TypedDict):
    direction: SwipeDirection


class FilterAppliedProperties(TypedDict):
    # The list of filter dimensions that were active in this request,
    # not the filter values themselves. Avoids leaking taste fingerprints
    # while still answering "which knobs do users actually move?".
    dimensions: list[FilterDimension]


class WatchedAddedProperties(TypedDict):
    tconst: str  # IMDb ID — public catalog identifier, not PII.


class WatchedRemovedProperties(TypedDict):
    tconst: str


class WatchlistAddedProperties(TypedDict):
    tconst: str


class WatchlistRemovedProperties(TypedDict):
    tconst: str


class SearchPerformedProperties(TypedDict):
    # Bucketed result count, never the raw query text. Raw queries can
    # contain unintended PII (people search for their own names) and they
    # explode property cardinality.
    result_count_bucket: SearchResultBucket


# ── User properties (set via identify) ────────────────────────────────
# Properties attached to the *person*, not to a single event. Set on
# signup, login, and OAuth completion.

class UserIdentifyProperties(TypedDict, total=False):
    auth_provider: AuthProvider
    signup_at: str  # ISO 8601 datetime; set on signup_completed.


# ── Helpers for cardinality-bucketing ─────────────────────────────────

def bucket_search_result_count(count: int) -> SearchResultBucket:
    """Bucket a raw search-result count into a closed-set label.

    Mirrors the cardinality-bounding pattern used by ``bucket_http_status``
    in ``infra/metrics.py`` — keeps the property dimension low-cardinality
    so funnel and breakdown charts stay legible.
    """
    if count <= 0:
        return "0"
    if count <= 5:
        return "1-5"
    if count <= 10:
        return "6-10"
    return "11+"


__all__ = [
    "AuthProvider",
    "EVENT_FILTER_APPLIED",
    "EVENT_LOGGED_OUT",
    "EVENT_LOGIN_FAILED",
    "EVENT_LOGIN_SUCCEEDED",
    "EVENT_MOVIE_SWIPED",
    "EVENT_OAUTH_FAILED",
    "EVENT_SEARCH_PERFORMED",
    "EVENT_SIGNUP_COMPLETED",
    "EVENT_SIGNUP_FAILED",
    "EVENT_WATCHED_ADDED",
    "EVENT_WATCHED_REMOVED",
    "EVENT_WATCHLIST_ADDED",
    "EVENT_WATCHLIST_REMOVED",
    "FilterAppliedProperties",
    "FilterDimension",
    "LoggedOutProperties",
    "LoginFailedProperties",
    "LoginFailureReason",
    "LoginSucceededProperties",
    "MovieSwipedProperties",
    "OAuthFailedProperties",
    "OAuthFailureReason",
    "SearchPerformedProperties",
    "SearchResultBucket",
    "SignupCompletedProperties",
    "SignupFailedProperties",
    "SignupFailureReason",
    "SwipeDirection",
    "UserIdentifyProperties",
    "WatchedAddedProperties",
    "WatchedRemovedProperties",
    "WatchlistAddedProperties",
    "WatchlistRemovedProperties",
    "bucket_search_result_count",
]
