"""Result contracts for projection enrichment persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(slots=True)
class EnrichmentResult:
    status: Literal["ready", "failed"]
    persistence_mode: Literal["READY_UPSERT", "READY_METADATA_ONLY", "FAILED_UPSERT"]
    payload: dict[str, Any]
    attempts: int
    tmdb_id: int | None
    error: str | None
    timestamp: datetime
