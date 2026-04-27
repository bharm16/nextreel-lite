"""Live movie title search — backs the Spotlight modal in the navbar."""

from __future__ import annotations

from quart import jsonify, request

from infra.event_schema import EVENT_SEARCH_PERFORMED, bucket_search_result_count
from infra.events import track_event
from infra.metrics import user_actions_total
from infra.route_helpers import rate_limited, with_timeout
from logging_config import get_logger
from movies.movie_url import build_movie_path
from movies.search_queries import build_search_query
from nextreel.web.routes.shared import (
    _REQUEST_TIMEOUT,
    _current_state,
    _distinct_id_for,
    _resolve_public_id_or_404,
    _services,
    bp,
)

logger = get_logger(__name__)

_SEARCH_LIMIT = 10
_TMDB_IMAGE_PREFIX = "https://image.tmdb.org/t/p/"


async def _execute_search(sql: str, params: list) -> list[dict]:
    """Run the title-search query against the pool. Extracted for test injection."""
    services = _services()
    # Pool is exposed through movie_manager (see shared.py _services() and
    # NextReelServices dataclass; pool is wired as movie_manager.db_pool).
    pool = services.movie_manager.db_pool
    rows = await pool.execute(sql, params, fetch="all")
    return rows or []


def _thumb_url(poster_url: str | None) -> str | None:
    """Downsize a stored TMDb poster URL (w500) to the spotlight thumb size (w92).

    Falls through unchanged for non-TMDb URLs so a future change to the stored
    format doesn't silently mangle them.
    """
    if not poster_url or not isinstance(poster_url, str):
        return None
    if not poster_url.startswith(_TMDB_IMAGE_PREFIX):
        return poster_url
    remainder = poster_url[len(_TMDB_IMAGE_PREFIX):]
    if "/" not in remainder:
        return poster_url
    _size, path = remainder.split("/", 1)
    if not path:
        return poster_url
    return f"{_TMDB_IMAGE_PREFIX}w92/{path}"


@bp.route("/api/search", methods=["GET"])
@rate_limited("search_titles")
@with_timeout(_REQUEST_TIMEOUT)
async def search_titles():
    """Live title search backing the Spotlight modal.

    Degrades gracefully — always returns 200 with a (possibly empty) results
    list, so the frontend UI never renders an error state mid-typing.
    """
    raw_query = request.args.get("q", "").strip()
    sql, params = build_search_query(raw_query, limit=_SEARCH_LIMIT)

    if sql is None:
        return jsonify({"results": []})

    # Only count requests that actually run a query — sql is None for empty
    # or unusable input, and counting those would inflate the metric with
    # navbar-mount noise and per-keystroke debouncer churn.
    user_actions_total.labels(action_type="search").inc()

    try:
        rows = await _execute_search(sql, params)
    except Exception as exc:  # noqa: BLE001 — defense-in-depth
        logger.warning("Search query failed for q=%r: %s", raw_query, exc)
        return jsonify({"results": []})

    # Build the canonical /movie/<slug>-<public_id> URL server-side and
    # ship it in the response. Frontend used to construct /movie/<tconst>,
    # which the new public_id router 404s on. Rows without a public_id
    # (candidate has no projection row yet) are skipped — they're not
    # navigable until enrichment runs.
    results = [
        {
            "title": row.get("primaryTitle"),
            "year": row.get("startYear"),
            "rating": float(row["averageRating"]) if row.get("averageRating") is not None else None,
            "poster_url": _thumb_url(row.get("poster_url")),
            "url": build_movie_path(
                row.get("primaryTitle"), row.get("startYear"), row["public_id"]
            ),
        }
        for row in rows
        if row.get("public_id")
    ]
    track_event(
        _distinct_id_for(_current_state()),
        EVENT_SEARCH_PERFORMED,
        {"result_count_bucket": bucket_search_result_count(len(results))},
    )
    return jsonify({"results": results})


@bp.route("/api/projection-state/<public_id>", methods=["GET"])
@with_timeout(_REQUEST_TIMEOUT)
async def projection_state(public_id):
    """Lightweight projection state probe — used by the movie page poller
    to detect when background enrichment has completed so it can refresh
    into the fully-populated view.
    """
    tconst = await _resolve_public_id_or_404(public_id)
    services = _services()
    row = await services.movie_manager.projection_store.select_row(tconst)
    state = row.get("projection_state") if row else None
    return jsonify({"state": state})
