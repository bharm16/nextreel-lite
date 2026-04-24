"""Watched-list route handlers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from quart import abort, jsonify, redirect, render_template, request, url_for

from infra.route_helpers import csrf_required, rate_limited, safe_referrer as _safe_referrer
from nextreel.web.routes.shared import (
    _TCONST_RE,
    _current_user_id,
    _letterboxd_import_service,
    _require_login,
    _services,
    _watched_list_presenter,
    _watched_progress_service,
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


def _parse_filter_params(args) -> dict:
    """Extract filter parameters from request query string."""
    result = {}

    decades_raw = args.get("decades", "")
    if decades_raw:
        result["decades"] = [d.strip().rstrip("s") for d in decades_raw.split(",") if d.strip()]

    rating_tier = args.get("rating", "")
    if rating_tier == "8+":
        result["rating_min"] = 8.0
        result["rating_max"] = 10.0
    elif rating_tier == "6-8":
        result["rating_min"] = 6.0
        result["rating_max"] = 7.99
    elif rating_tier == "<6":
        result["rating_min"] = 0.0
        result["rating_max"] = 5.99

    genres_raw = args.get("genres", "")
    if genres_raw:
        result["genres"] = [g.strip() for g in genres_raw.split(",") if g.strip()]

    return result


_VALID_SORTS = {"recent", "title_asc", "title_desc", "year_desc", "rating_desc"}


@bp.route("/watched")
async def watched_list_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()
    watched_store = services.movie_manager.watched_store

    page, per_page, offset = _parse_watched_pagination(request.args)
    sort = request.args.get("sort", "recent")
    if sort not in _VALID_SORTS:
        sort = "recent"
    filter_params = _parse_filter_params(request.args)

    from quart import session as quart_session

    enrichment_pending = quart_session.get("letterboxd_enrichment_pending", False)

    raw_rows, total_count, filter_chips = await asyncio.gather(
        watched_store.list_watched_filtered(
            user_id, sort=sort, limit=per_page, offset=offset, **filter_params
        ),
        watched_store.count_filtered(user_id, **filter_params),
        watched_store.available_filter_chips(user_id),
    )

    view_model = _watched_list_presenter.build(
        raw_rows=raw_rows,
        total_count=total_count,
        page=page,
        per_page=per_page,
        now=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    has_more = (offset + per_page) < total_count

    if _wants_json_response():
        from quart import render_template as rt

        html_parts = [
            await rt("_watched_card.html", movie=movie) for movie in view_model.movies
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
        "watched_list.html",
        movies=view_model.movies,
        total=view_model.total,
        filter_chips=filter_chips,
        has_more=has_more,
        pagination=view_model.pagination,
        enrichment_pending=enrichment_pending,
        current_sort=sort,
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
    await services.movie_manager.watched_store.add(user_id, tconst)
    logger.info("User %s marked %s as watched", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_watched": True,
                "tconst": tconst,
            }
        )

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
    await services.movie_manager.watched_store.remove(user_id, tconst)
    logger.info("User %s removed %s from watched", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_watched": False,
                "tconst": tconst,
            }
        )

    return redirect(_safe_referrer(tconst), code=303)


@bp.route("/watched/import-letterboxd", methods=["POST"])
@csrf_required
async def import_letterboxd():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    from quart import current_app, flash, session as quart_session

    user_id = _current_user_id()
    services = _services()

    files = await request.files
    uploaded = files.get("letterboxd_csv")

    try:
        outcome = await _letterboxd_import_service.import_watched(
            user_id=user_id,
            uploaded=uploaded,
            db_pool=services.movie_manager.db_pool,
            watched_store=services.movie_manager.watched_store,
            enqueue_fn=getattr(current_app, "enqueue_runtime_job", None),
        )
    except Exception:
        logger.exception("Letterboxd import failed for user %s", user_id)
        await flash("Something went wrong during import. Please try again.", "error")
        return redirect(url_for("main.watched_list_page"))

    if outcome.kind == "success":
        if outcome.enrichment_requested:
            quart_session["letterboxd_import_tconsts"] = outcome.matched
            quart_session["letterboxd_enrichment_pending"] = True
            quart_session["letterboxd_sent_tconsts"] = []
        if outcome.unmatched_labels:
            quart_session["letterboxd_unmatched"] = outcome.unmatched_labels

        logger.info(
            "Letterboxd import for user %s: %d matched, %d unmatched",
            user_id,
            len(outcome.matched),
            len(outcome.unmatched_labels),
        )

    await flash(outcome.flash_message, outcome.flash_category)
    return redirect(url_for("main.watched_list_page"))


@bp.route("/watched/enrichment-progress")
async def enrichment_progress():
    redirect_response = _require_login()
    if redirect_response:
        return jsonify({"html": "", "new_count": 0, "total_ready": 0, "total": 0, "done": True})

    from quart import session as quart_session

    services = _services()
    progress = await _watched_progress_service.progress(
        session_state=quart_session,
        user_id=_current_user_id(),
        watched_store=services.movie_manager.watched_store,
        presenter=_watched_list_presenter,
        now=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    html_parts = [
        await render_template("_watched_card.html", movie=movie) for movie in progress.new_movies
    ]

    return jsonify(
        {
            "html": "".join(html_parts),
            "new_count": progress.new_count,
            "total_ready": progress.total_ready,
            "total": progress.total,
            "done": progress.done,
        }
    )


__all__ = [
    "add_to_watched",
    "enrichment_progress",
    "import_letterboxd",
    "remove_from_watched",
    "watched_list_page",
]
