"""Projection state enum and transition policy.

Extracted from projection_store.py to make the state machine explicit
rather than relying on string comparisons scattered across methods.
"""

from __future__ import annotations

from datetime import timedelta
from enum import Enum


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
