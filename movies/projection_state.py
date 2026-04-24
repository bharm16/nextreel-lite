"""Projection state enum, transition policy, and result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal


class ProjectionState(Enum):
    """Explicit projection lifecycle states.

    Transition graph::

        CORE  ──enrich──►  READY
          │                  │
          │                  │ (stale_after elapsed)
          │                  ▼
          │                STALE  ──re-enrich──►  READY
          │                  │
          ▼                  ▼
        FAILED ◄──enrich fails──  (any)
    """

    CORE = "core"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"

    def can_serve(self) -> bool:
        """Whether this state's payload is suitable for rendering."""
        return self in (ProjectionState.READY, ProjectionState.STALE)

    def needs_enrichment(self) -> bool:
        """Whether this state should trigger an enrichment attempt."""
        return self in (ProjectionState.CORE, ProjectionState.STALE, ProjectionState.FAILED)


# Enrichment policy constants
ENQUEUE_COOLDOWN = timedelta(minutes=15)
STALE_AFTER = timedelta(days=7)
FAILED_RETRY_COOLDOWN = timedelta(hours=6)


@dataclass(slots=True)
class EnrichmentResult:
    status: Literal["ready", "failed"]
    persistence_mode: Literal["READY_UPSERT", "READY_METADATA_ONLY", "FAILED_UPSERT"]
    payload: dict[str, Any]
    attempts: int
    tmdb_id: int | None
    error: str | None
    timestamp: datetime
