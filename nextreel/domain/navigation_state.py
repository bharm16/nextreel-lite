from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nextreel.domain.filter_contracts import FilterState

SESSION_COOKIE_NAME = "nr_sid"
SESSION_COOKIE_MAX_AGE = 8 * 60 * 60
QUEUE_TARGET = 5
QUEUE_REFILL_THRESHOLD = 2
PREV_STACK_MAX = 20
FUTURE_STACK_MAX = 20
SEEN_MAX = 50


def _normalize_ref(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    tconst = entry.get("tconst") or entry.get("imdb_id")
    if not tconst:
        return None
    return {
        "tconst": tconst,
        "title": entry.get("title"),
        "slug": entry.get("slug"),
    }


def _normalize_ref_list(entries: list[Any], *, max_items: int) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for entry in entries or []:
        ref = _normalize_ref(entry)
        if ref:
            refs.append(ref)
    return refs[:max_items]


def _normalize_seen(entries: list[Any]) -> list[str]:
    seen: list[str] = []
    for entry in entries or []:
        if isinstance(entry, str) and entry:
            seen.append(entry)
    return seen[-SEEN_MAX:]


@dataclass
class NavigationState:
    session_id: str
    version: int
    csrf_token: str
    filters: FilterState
    current_tconst: str | None
    queue: list[dict[str, Any]]
    prev: list[dict[str, Any]]
    future: list[dict[str, Any]]
    seen: list[str]
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    current_ref: dict[str, Any] | None = None
    user_id: str | None = None
    _serialized_cache: dict[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def clone(self) -> "NavigationState":
        return NavigationState(
            session_id=self.session_id,
            version=self.version,
            csrf_token=self.csrf_token,
            filters=dict(self.filters) if isinstance(self.filters, dict) else self.filters,
            current_tconst=self.current_tconst,
            queue=[dict(item) for item in self.queue],
            prev=[dict(item) for item in self.prev],
            future=[dict(item) for item in self.future],
            seen=list(self.seen),
            created_at=self.created_at,
            last_activity_at=self.last_activity_at,
            expires_at=self.expires_at,
            current_ref=dict(self.current_ref) if self.current_ref else None,
            user_id=self.user_id,
        )


@dataclass
class MutationResult:
    state: NavigationState | None
    result: Any = None
    conflicted: bool = False
