"""Compatibility exports for navigation state modules.

The model, repository, and service were split into focused modules. This file
keeps existing imports working while preventing new ownership from collecting
here again.
"""

from __future__ import annotations

from infra.cache import CacheNamespace
from infra.filter_normalizer import (
    MAX_FILTER_VALUE_LEN,
    criteria_from_filters,
    default_filter_state,
    filters_from_criteria,
    normalize_filters,
    validate_filters,
)
from infra.navigation_state_repository import NavigationStateRepository
from infra.time_utils import env_bool, env_int, utcnow
from nextreel.application.navigation_state_service import (
    NavigationStateService,
    NavigationStateStore,
    _idle_timeout,
    _max_duration,
)
from nextreel.domain.navigation_state import (
    FUTURE_STACK_MAX,
    PREV_STACK_MAX,
    QUEUE_REFILL_THRESHOLD,
    QUEUE_TARGET,
    SEEN_MAX,
    SESSION_COOKIE_MAX_AGE,
    SESSION_COOKIE_NAME,
    MutationResult,
    NavigationState,
    _normalize_ref,
    _normalize_ref_list,
    _normalize_seen,
)

__all__ = [
    "CacheNamespace",
    "FUTURE_STACK_MAX",
    "MAX_FILTER_VALUE_LEN",
    "MutationResult",
    "NavigationState",
    "NavigationStateRepository",
    "NavigationStateService",
    "NavigationStateStore",
    "PREV_STACK_MAX",
    "QUEUE_REFILL_THRESHOLD",
    "QUEUE_TARGET",
    "SEEN_MAX",
    "SESSION_COOKIE_MAX_AGE",
    "SESSION_COOKIE_NAME",
    "_idle_timeout",
    "_max_duration",
    "_normalize_ref",
    "_normalize_ref_list",
    "_normalize_seen",
    "criteria_from_filters",
    "default_filter_state",
    "env_bool",
    "env_int",
    "filters_from_criteria",
    "normalize_filters",
    "utcnow",
    "validate_filters",
]
