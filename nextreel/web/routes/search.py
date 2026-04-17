"""Live movie title search — backs the Spotlight modal in the navbar."""

from __future__ import annotations

from quart import jsonify, request

from infra.route_helpers import rate_limited, with_timeout
from logging_config import get_logger
from movies.query_builder import MovieQueryBuilder
from nextreel.web.routes.shared import _REQUEST_TIMEOUT, _services, bp

logger = get_logger(__name__)

_SEARCH_LIMIT = 10


async def _execute_search(sql: str, params: list) -> list[dict]:
    """Run the title-search query against the pool. Extracted for test injection."""
    services = _services()
    # Pool is exposed through movie_manager (see shared.py _services() and
    # NextReelServices dataclass; pool is wired as movie_manager.db_pool).
    pool = services.movie_manager.db_pool
    rows = await pool.execute(sql, params, fetch="all")
    return rows or []


@bp.route("/api/search", methods=["GET"])
@rate_limited("search_titles")
@with_timeout(_REQUEST_TIMEOUT)
async def search_titles():
    """Live title search backing the Spotlight modal.

    Degrades gracefully — always returns 200 with a (possibly empty) results
    list, so the frontend UI never renders an error state mid-typing.
    """
    raw_query = request.args.get("q", "").strip()
    sql, params = MovieQueryBuilder.build_search_query(raw_query, limit=_SEARCH_LIMIT)

    if sql is None:
        return jsonify({"results": []})

    try:
        rows = await _execute_search(sql, params)
    except Exception as exc:  # noqa: BLE001 — defense-in-depth
        logger.warning("Search query failed for q=%r: %s", raw_query, exc)
        return jsonify({"results": []})

    results = [
        {
            "tconst": row.get("tconst"),
            "title": row.get("primaryTitle"),
            "year": row.get("startYear"),
            "rating": float(row["averageRating"]) if row.get("averageRating") is not None else None,
        }
        for row in rows
    ]
    return jsonify({"results": results})
