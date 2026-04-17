"""Random-film picker for the Criterion-style landing page.

Queries movie_projection for one READY row whose payload carries a real
TMDb backdrop URL, and returns a flat dict ready for template rendering.
Separate from projection_read_service because its concern (landing hero
selection) has no relationship to the stateful render-policy logic there.
"""

from __future__ import annotations

import json
from typing import Any

from logging_config import get_logger

logger = get_logger(__name__)

_LANDING_SENTINELS = ("Unknown", "N/A", "", "0 min")

_LANDING_SQL = (
    "SELECT tconst, payload_json "
    "FROM movie_projection "
    "WHERE projection_state = 'ready' "
    "  AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.backdrop_url')) LIKE 'https://image.tmdb.org/%' "
    "ORDER BY RAND() "
    "LIMIT 1"
)


def _clean(value: Any) -> Any:
    """Return None for the payload_factory's 'missing-field' sentinels."""
    if value is None or value in _LANDING_SENTINELS:
        return None
    return value


async def fetch_random_landing_film(pool) -> dict[str, Any] | None:
    """Pick one enriched film with a TMDb-sourced backdrop, at random.

    Returns a flat dict ready for template use, or None if no qualifying
    rows exist. Callers should apply a hardcoded fallback pool when None.
    """
    try:
        rows = await pool.execute(_LANDING_SQL, (), fetch="all")
    except Exception as exc:  # noqa: BLE001 — defense-in-depth, degrade silently
        logger.warning("Landing-film query failed: %s", exc)
        return None
    if not rows:
        return None

    row = rows[0]
    payload_raw = row["payload_json"]
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw

    return {
        "tconst": row["tconst"],
        "title": payload.get("title"),
        "year": _clean(payload.get("year")),
        "director": _clean(payload.get("directors")),
        "runtime": _clean(payload.get("runtime")),
        "backdrop_url": payload.get("backdrop_url"),
    }
