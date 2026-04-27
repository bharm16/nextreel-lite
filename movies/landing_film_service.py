"""Random-film picker for the Criterion-style landing page.

Queries movie_projection for one READY row whose payload carries a real
TMDb backdrop URL, and returns a flat dict ready for template rendering.
Separate from projection_read_service because its concern (landing hero
selection) has no relationship to the stateful render-policy logic there.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

from logging_config import get_logger

logger = get_logger(__name__)

_LANDING_SENTINELS = ("Unknown", "N/A", "", "0 min")

# The earlier implementation ran:
#   WHERE ... AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.backdrop_url'))
#              LIKE 'https://image.tmdb.org/%'
#   ORDER BY RAND()
#   LIMIT 1
#
# Both the JSON predicate and ORDER BY RAND() force a full table scan on
# movie_projection (hundreds of thousands of rows) and a sort of every
# matched row for a single output. On the hot landing-page path this
# regularly saturated the DB pool.
#
# New strategy: cheap random-offset pick with Python-side backdrop
# validation. The READY-row count is index-only via
# idx_movie_projection_state_stale and is memoised in-process for a
# short TTL. We fetch a small pool of candidates at a random offset and
# return the first one whose backdrop is a real TMDb URL. In steady
# state almost every READY row has a real backdrop, so this is
# effectively a single indexed lookup.

_LANDING_TMDB_PREFIX = "https://image.tmdb.org/"
_LANDING_CANDIDATE_POOL_SIZE = 20
_READY_COUNT_TTL_SECONDS = 300.0
_READY_COUNT_CACHE: dict[str, float | int] = {"value": 0, "expires_at": 0.0}


def _clean(value: Any) -> Any:
    """Return None for the payload_factory's 'missing-field' sentinels."""
    if value is None or value in _LANDING_SENTINELS:
        return None
    return value


async def _ready_row_count(pool) -> int:
    """Count READY movie_projection rows with short-TTL in-process cache.

    The COUNT is served by ``idx_movie_projection_state_stale`` and is
    fast, but the landing page gets hit on every anonymous session. A
    5-minute cache cuts the query out of the hot path entirely without
    meaningfully skewing the random distribution (new READY rows land
    at the tail, and the offset we pick is uniform regardless).
    """
    now = time.monotonic()
    if _READY_COUNT_CACHE["expires_at"] > now:
        return int(_READY_COUNT_CACHE["value"])
    row = await pool.execute(
        "SELECT COUNT(*) AS n FROM movie_projection " "WHERE projection_state = 'ready'",
        fetch="one",
    )
    count = int(row["n"]) if row and row.get("n") is not None else 0
    _READY_COUNT_CACHE["value"] = count
    _READY_COUNT_CACHE["expires_at"] = now + _READY_COUNT_TTL_SECONDS
    return count


def _reset_ready_count_cache() -> None:
    """Testing hook — callers should not use this in production paths."""
    _READY_COUNT_CACHE["value"] = 0
    _READY_COUNT_CACHE["expires_at"] = 0.0


async def fetch_random_landing_film(
    pool, criteria: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Pick one enriched film with a TMDb-sourced backdrop, at random.

    When ``criteria`` is None or empty, uses the fast unfiltered offset path
    against all READY rows. When ``criteria`` is provided, routes through a
    filter-aware path that constrains rows by genre / year range / runtime /
    rating before the random pick.

    Returns a flat dict ready for template use, or None if no qualifying
    rows exist. Callers should apply a hardcoded fallback only on the
    unfiltered path; on the filtered path, None signals empty-state UX.
    """
    if not criteria:
        return await _fetch_random_unfiltered(pool)
    return await _fetch_random_filtered(pool, criteria)


async def _fetch_random_unfiltered(pool) -> dict[str, Any] | None:
    """Existing fast offset-based random pick across all READY rows."""
    try:
        total = await _ready_row_count(pool)
    except Exception as exc:  # noqa: BLE001 — defend the landing page
        logger.warning("Landing-film count query failed: %s", exc)
        return None

    if total <= 0:
        return None

    offset = random.randint(0, max(0, total - _LANDING_CANDIDATE_POOL_SIZE))
    try:
        # Step 1: pick the candidate tconsts using an index-only scan.
        # Ordering the projection table directly by tconst with a LIMIT/OFFSET
        # forces MySQL to filesort on the wide payload_json TEXT column and
        # the sort_buffer_size runs out — see error 1038. Selecting just
        # tconst in the ORDER BY pipeline keeps rows narrow enough to sort
        # in memory; fetching the JSON payloads in a second keyed lookup
        # avoids the filesort entirely.
        id_rows = await pool.execute(
            """
            SELECT tconst
            FROM movie_projection
            WHERE projection_state = 'ready'
            ORDER BY tconst
            LIMIT %s OFFSET %s
            """,
            (_LANDING_CANDIDATE_POOL_SIZE, offset),
            fetch="all",
        )
    except Exception as exc:  # noqa: BLE001 — defense-in-depth, degrade silently
        logger.warning("Landing-film tconst query failed: %s", exc)
        return None

    return await _hydrate_first_with_real_backdrop(pool, id_rows)


async def _fetch_random_filtered(pool, criteria: dict[str, Any]) -> dict[str, Any] | None:
    """Filter-aware random pick.

    Constrains ``movie_projection`` rows by joining ``movie_candidates`` for
    genre/runtime/rating predicates and applying year-range against the
    payload's stringified year. Then takes a random offset within the
    matching set, hydrates payloads, and returns the first one with a real
    backdrop.
    """
    where_clauses = ["mp.projection_state = 'ready'"]
    params: list[Any] = []

    # The landing strip is single-select per dimension, so genres is at most a
    # one-element list. If a caller ever passes a multi-genre criteria from
    # somewhere else (e.g. the full filter UI), only the first is applied.
    genres = criteria.get("genres")
    if isinstance(genres, list) and genres:
        where_clauses.append("FIND_IN_SET(%s, mc.genres) > 0")
        params.append(genres[0])

    if "min_year" in criteria:
        where_clauses.append(
            "CAST(JSON_UNQUOTE(JSON_EXTRACT(mp.payload_json, '$.year')) AS UNSIGNED) >= %s"
        )
        params.append(int(criteria["min_year"]))
    if "max_year" in criteria:
        where_clauses.append(
            "CAST(JSON_UNQUOTE(JSON_EXTRACT(mp.payload_json, '$.year')) AS UNSIGNED) <= %s"
        )
        params.append(int(criteria["max_year"]))

    if "max_runtime" in criteria:
        where_clauses.append("mc.runtimeMinutes <= %s")
        params.append(int(criteria["max_runtime"]))
    if "min_runtime" in criteria:
        where_clauses.append("mc.runtimeMinutes >= %s")
        params.append(int(criteria["min_runtime"]))

    if "min_rating" in criteria:
        where_clauses.append("mc.averageRating >= %s")
        params.append(float(criteria["min_rating"]))

    # where_clauses contains only static SQL structure (column names, operators).
    # Never interpolate criteria values into where_clauses — all user-supplied
    # values must go through the params list and bind via %s placeholders below.
    where_sql = " AND ".join(where_clauses)

    # Cold count — the filtered total varies per filter combination, so the
    # _ready_row_count cache (which is unfiltered-only) must not be reused
    # here. A fresh COUNT runs per call. User-paced clicks make this
    # acceptable; bursty hot-path callers would need their own caching.
    count_sql = (
        "SELECT COUNT(*) AS n "
        "FROM movie_projection mp "
        "JOIN movie_candidates mc ON mc.tconst = mp.tconst "
        f"WHERE {where_sql}"
    )

    try:
        count_row = await pool.execute(count_sql, params, fetch="one")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Landing-film filtered count failed: %s", exc)
        return None

    total = int(count_row["n"]) if count_row and count_row.get("n") is not None else 0
    if total <= 0:
        return None

    offset = random.randint(0, max(0, total - _LANDING_CANDIDATE_POOL_SIZE))
    id_sql = (
        "SELECT mp.tconst "
        "FROM movie_projection mp "
        "JOIN movie_candidates mc ON mc.tconst = mp.tconst "
        f"WHERE {where_sql} "
        "ORDER BY mp.tconst "
        "LIMIT %s OFFSET %s"
    )
    id_params = (*params, _LANDING_CANDIDATE_POOL_SIZE, offset)

    try:
        id_rows = await pool.execute(id_sql, id_params, fetch="all")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Landing-film filtered tconst query failed: %s", exc)
        return None

    return await _hydrate_first_with_real_backdrop(pool, id_rows)


async def _hydrate_first_with_real_backdrop(pool, id_rows) -> dict[str, Any] | None:
    """Common payload hydration step shared by filtered and unfiltered paths."""
    if not id_rows:
        return None

    tconsts = [row["tconst"] for row in id_rows if row.get("tconst")]
    if not tconsts:
        return None

    placeholders = ",".join(["%s"] * len(tconsts))
    try:
        rows = await pool.execute(
            f"""
            SELECT tconst, payload_json, public_id
            FROM movie_projection
            WHERE tconst IN ({placeholders})
            """,
            tconsts,
            fetch="all",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Landing-film payload query failed: %s", exc)
        return None

    if not rows:
        return None

    # Walk the fetched window in a shuffled order so repeated hits with
    # the same offset don't always serve the same film.
    rows = list(rows)
    random.shuffle(rows)
    for row in rows:
        payload_raw = row["payload_json"]
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        backdrop_url = payload.get("backdrop_url")
        if not isinstance(backdrop_url, str) or not backdrop_url.startswith(_LANDING_TMDB_PREFIX):
            continue
        return {
            "tconst": row["tconst"],
            "public_id": row.get("public_id"),
            "title": payload.get("title"),
            "year": _clean(payload.get("year")),
            "director": _clean(payload.get("directors")),
            "runtime": _clean(payload.get("runtime")),
            "backdrop_url": backdrop_url,
        }

    return None
