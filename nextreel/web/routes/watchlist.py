"""Watchlist (save-for-later) route handlers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from quart import abort, jsonify, redirect, render_template, request

from infra.event_schema import EVENT_WATCHLIST_ADDED, EVENT_WATCHLIST_REMOVED
from infra.events import track_event
from infra.metrics import user_actions_total
from infra.route_helpers import csrf_required, rate_limited, safe_referrer as _safe_referrer
from nextreel.web.routes.shared import (
    LIST_VALID_SORTS,
    _current_user_id,
    _require_login,
    _resolve_public_id_or_404,
    _services,
    _watchlist_list_presenter,
    _wants_json_response,
    bp,
    logger,
    parse_list_filter_params,
    parse_list_pagination,
)


@bp.route("/watchlist")
async def watchlist_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()
    watchlist_store = services.movie_manager.watchlist_store

    page, per_page, offset = parse_list_pagination(request.args)
    sort = request.args.get("sort", "recent")
    if sort not in LIST_VALID_SORTS:
        sort = "recent"
    filter_params = parse_list_filter_params(request.args)

    raw_rows, total_count, filter_chips = await asyncio.gather(
        watchlist_store.list_watchlist_filtered(
            user_id, sort=sort, limit=per_page, offset=offset, **filter_params
        ),
        watchlist_store.count_filtered(user_id, **filter_params),
        watchlist_store.available_filter_chips(user_id),
    )

    view_model = _watchlist_list_presenter.build(
        raw_rows=raw_rows,
        total_count=total_count,
        page=page,
        per_page=per_page,
        now=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    has_more = (offset + per_page) < total_count

    if _wants_json_response():
        html_parts = [
            await render_template("_watchlist_card.html", movie=movie)
            for movie in view_model.movies
        ]
        return jsonify(
            {
                "html": "".join(html_parts),
                "total": total_count,
                "has_more": has_more,
                "page": page,
            }
        )

    return await render_template(
        "watchlist.html",
        movies=view_model.movies,
        total=view_model.total,
        filter_chips=filter_chips,
        has_more=has_more,
        pagination=view_model.pagination,
        current_sort=sort,
    )


@bp.route("/watchlist/add/<public_id>", methods=["POST"])
@csrf_required
@rate_limited("watchlist")
async def add_to_watchlist(public_id):
    tconst = await _resolve_public_id_or_404(public_id)
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watchlist_store.add(user_id, tconst)
    user_actions_total.labels(action_type="watchlist_add").inc()
    track_event(user_id, EVENT_WATCHLIST_ADDED, {"tconst": tconst})
    logger.info("User %s added %s to watchlist", user_id, tconst)
    if _wants_json_response():
        # public_id is the opaque client-facing key; tconst is intentionally
        # omitted so the API doesn't perpetuate external dependence on the
        # internal IMDb identifier.
        return jsonify({"ok": True, "is_in_watchlist": True, "public_id": public_id})

    return redirect(await _safe_referrer(tconst), code=303)


@bp.route("/watchlist/remove/<public_id>", methods=["POST"])
@csrf_required
@rate_limited("watchlist")
async def remove_from_watchlist(public_id):
    tconst = await _resolve_public_id_or_404(public_id)
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watchlist_store.remove(user_id, tconst)
    user_actions_total.labels(action_type="watchlist_remove").inc()
    track_event(user_id, EVENT_WATCHLIST_REMOVED, {"tconst": tconst})
    logger.info("User %s removed %s from watchlist", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_in_watchlist": False,
                "public_id": public_id,
            }
        )

    return redirect(await _safe_referrer(tconst), code=303)


__all__ = [
    "add_to_watchlist",
    "remove_from_watchlist",
    "watchlist_page",
]
