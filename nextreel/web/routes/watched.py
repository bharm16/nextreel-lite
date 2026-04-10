"""Watched-list route handlers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from quart import abort, jsonify, redirect, render_template, request

from infra.route_helpers import csrf_required, rate_limited, safe_referrer as _safe_referrer
from nextreel.web.routes.shared import (
    _TCONST_RE,
    _current_user_id,
    _require_login,
    _services,
    _watched_list_presenter,
    _watched_mutation_service,
    _wants_json_response,
    bp,
    logger,
)


def _parse_watched_pagination(args) -> tuple[int, int, int]:
    try:
        page = max(1, int(args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(args.get("per_page", 60))
    except (TypeError, ValueError):
        per_page = 60
    per_page = max(1, min(per_page, 200))
    offset = (page - 1) * per_page
    return page, per_page, offset


@bp.route("/watched")
async def watched_list_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()

    page, per_page, offset = _parse_watched_pagination(request.args)

    raw_rows, total_count = await asyncio.gather(
        services.movie_manager.watched_store.list_watched(
            user_id, limit=per_page, offset=offset
        ),
        services.movie_manager.watched_store.count(user_id),
    )

    view_model = _watched_list_presenter.build(
        raw_rows=raw_rows,
        total_count=total_count,
        page=page,
        per_page=per_page,
        now=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    return await render_template(
        "watched_list.html",
        movies=view_model.movies,
        stats=view_model.stats,
        total=view_model.total,
        pagination=view_model.pagination,
    )


@bp.route("/watched/add/<tconst>", methods=["POST"])
@csrf_required
@rate_limited("watched")
async def add_to_watched(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await _watched_mutation_service.add(
        user_id=user_id,
        tconst=tconst,
        watched_store=services.movie_manager.watched_store,
    )
    logger.info("User %s marked %s as watched", user_id, tconst)
    if _wants_json_response():
        return jsonify({
            "ok": True,
            "is_watched": True,
            "tconst": tconst,
        })

    return redirect(_safe_referrer(tconst), code=303)


@bp.route("/watched/remove/<tconst>", methods=["POST"])
@csrf_required
@rate_limited("watched")
async def remove_from_watched(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await _watched_mutation_service.remove(
        user_id=user_id,
        tconst=tconst,
        watched_store=services.movie_manager.watched_store,
    )
    logger.info("User %s removed %s from watched", user_id, tconst)
    if _wants_json_response():
        return jsonify({
            "ok": True,
            "is_watched": False,
            "tconst": tconst,
        })

    return redirect(_safe_referrer(tconst), code=303)

__all__ = ["add_to_watched", "remove_from_watched", "watched_list_page"]
