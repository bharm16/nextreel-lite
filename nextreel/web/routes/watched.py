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
    _watched_mutation_service,
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


@bp.route("/watched")
async def watched_list_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()

    page, per_page, offset = _parse_watched_pagination(request.args)

    from quart import session as quart_session

    enrichment_pending = quart_session.get("letterboxd_enrichment_pending", False)

    if enrichment_pending:
        raw_rows, total_count = await asyncio.gather(
            services.movie_manager.watched_store.list_watched_enriched(
                user_id, limit=per_page, offset=offset
            ),
            services.movie_manager.watched_store.count_enriched(user_id),
        )
    else:
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
        enrichment_pending=enrichment_pending,
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
    await _watched_mutation_service.remove(
        user_id=user_id,
        tconst=tconst,
        watched_store=services.movie_manager.watched_store,
    )
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
