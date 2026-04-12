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

    # Fire non-blocking enrichment for un-enriched movies
    from quart import current_app
    from movies.letterboxd_import import enqueue_import_enrichment

    enqueue_fn = getattr(current_app, "enqueue_runtime_job", None)
    if enqueue_fn and result.matched:
        asyncio.create_task(
            enqueue_import_enrichment(
                result.matched,
                services.movie_manager.db_pool,
                enqueue_fn,
            )
        )
        quart_session["letterboxd_import_tconsts"] = result.matched
        quart_session["letterboxd_enrichment_pending"] = True
        quart_session["letterboxd_sent_tconsts"] = []

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


@bp.route("/watched/enrichment-progress")
async def enrichment_progress():
    redirect_response = _require_login()
    if redirect_response:
        return jsonify({"html": "", "new_count": 0, "total_ready": 0, "total": 0, "done": True})

    from quart import session as quart_session

    import_tconsts = quart_session.get("letterboxd_import_tconsts", [])
    if not import_tconsts:
        return jsonify({"html": "", "new_count": 0, "total_ready": 0, "total": 0, "done": True})

    sent_tconsts = set(quart_session.get("letterboxd_sent_tconsts", []))
    services = _services()

    # Find newly READY tconsts we haven't sent yet
    unsent = [tc for tc in import_tconsts if tc not in sent_tconsts]
    if not unsent:
        # All have been sent already — we're done
        quart_session.pop("letterboxd_enrichment_pending", None)
        quart_session.pop("letterboxd_import_tconsts", None)
        quart_session.pop("letterboxd_sent_tconsts", None)
        return jsonify({
            "html": "", "new_count": 0,
            "total_ready": len(sent_tconsts), "total": len(import_tconsts),
            "done": True,
        })

    # Query which unsent tconsts are now READY
    placeholders = ", ".join(["%s"] * len(unsent))
    ready_rows = await services.movie_manager.db_pool.execute(
        "SELECT tconst FROM movie_projection "
        "WHERE tconst IN (" + placeholders + ") "
        "AND projection_state = %s",
        [*unsent, "ready"],
        fetch="all",
    )
    newly_ready = {row["tconst"] for row in ready_rows} if ready_rows else set()

    if not newly_ready:
        total_ready = len(sent_tconsts)
        total = len(import_tconsts)
        return jsonify({
            "html": "", "new_count": 0,
            "total_ready": total_ready, "total": total,
            "done": False,
        })

    # Fetch full movie data for newly ready tconsts
    newly_ready_list = sorted(newly_ready)
    placeholders2 = ", ".join(["%s"] * len(newly_ready_list))
    rows = await services.movie_manager.db_pool.execute(
        "SELECT w.tconst, w.watched_at, "
        "c.primaryTitle, c.startYear, c.genres, c.slug, "
        "p.payload_json "
        "FROM user_watched_movies w "
        "INNER JOIN movie_projection p ON w.tconst = p.tconst "
        "LEFT JOIN movie_candidates c ON w.tconst = c.tconst "
        "WHERE w.tconst IN (" + placeholders2 + ") "
        "AND w.user_id = %s "
        "AND p.projection_state = %s "
        "ORDER BY w.watched_at DESC",
        [*newly_ready_list, _current_user_id(), "ready"],
        fetch="all",
    )

    # Build movie dicts using the presenter and render card partials
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    html_parts = []
    if rows:
        for row in rows:
            movie, _, _ = _watched_list_presenter._normalize_row(row, now)
            if movie:
                html_parts.append(
                    await render_template("_watched_card.html", movie=movie)
                )

    # Update sent tracking
    new_sent = sent_tconsts | newly_ready
    quart_session["letterboxd_sent_tconsts"] = list(new_sent)

    total_ready = len(new_sent)
    total = len(import_tconsts)
    done = total_ready >= total

    if done:
        quart_session.pop("letterboxd_enrichment_pending", None)
        quart_session.pop("letterboxd_import_tconsts", None)
        quart_session.pop("letterboxd_sent_tconsts", None)

    return jsonify({
        "html": "".join(html_parts),
        "new_count": len(newly_ready),
        "total_ready": total_ready,
        "total": total,
        "done": done,
    })


__all__ = [
    "add_to_watched",
    "enrichment_progress",
    "import_letterboxd",
    "remove_from_watched",
    "watched_list_page",
]
