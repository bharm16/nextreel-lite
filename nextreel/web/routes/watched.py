"""Watched-list route handlers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from quart import abort, jsonify, redirect, render_template, request, url_for

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


@bp.route("/watched/import-letterboxd", methods=["POST"])
@csrf_required
async def import_letterboxd():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    from quart import flash, session as quart_session

    user_id = _current_user_id()
    services = _services()

    files = await request.files
    uploaded = files.get("letterboxd_csv")
    if not uploaded or not uploaded.filename:
        await flash("Please select a CSV file.", "error")
        return redirect(url_for("main.watched_list_page"))

    # Check file size (5MB limit)
    file_bytes = uploaded.stream.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        await flash("File is too large. Maximum size is 5MB.", "error")
        return redirect(url_for("main.watched_list_page"))

    from movies.letterboxd_import import match_films, parse_watched_csv

    import io

    try:
        films = parse_watched_csv(io.BytesIO(file_bytes))
    except ValueError as exc:
        await flash(
            "Invalid CSV format: %s. Please upload the watched.csv from your Letterboxd export." % exc,
            "error",
        )
        return redirect(url_for("main.watched_list_page"))

    if not films:
        await flash("The CSV file contained no films.", "warning")
        return redirect(url_for("main.watched_list_page"))

    try:
        result = await match_films(
            services.movie_manager.db_pool,
            films,
        )
        added = await services.movie_manager.watched_store.add_bulk(
            user_id, result.matched
        )
    except Exception:
        logger.exception("Letterboxd import failed for user %s", user_id)
        await flash("Something went wrong during import. Please try again.", "error")
        return redirect(url_for("main.watched_list_page"))

    # Build flash message
    matched_count = len(result.matched)
    unmatched_count = len(result.unmatched)
    if unmatched_count:
        await flash(
            "Imported %d films. %d could not be matched." % (matched_count, unmatched_count),
            "success",
        )
        quart_session["letterboxd_unmatched"] = [
            "%s (%s)" % (u["name"], u["year"]) for u in result.unmatched[:50]
        ]
    else:
        await flash("Imported all %d films." % matched_count, "success")

    logger.info(
        "Letterboxd import for user %s: %d matched, %d unmatched",
        user_id,
        matched_count,
        unmatched_count,
    )
    return redirect(url_for("main.watched_list_page"))

__all__ = ["add_to_watched", "import_letterboxd", "remove_from_watched", "watched_list_page"]
