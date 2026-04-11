"""Navigation and filtering route handlers."""

from __future__ import annotations

import time

from quart import flash, g, jsonify, redirect, request, url_for

from infra.metrics import user_actions_total
from infra.navigation_state import normalize_filters, validate_filters
from infra.route_helpers import csrf_required, rate_limited, with_timeout
from nextreel.domain.filter_contracts import FilterState
from nextreel.web.routes.shared import (
    _REQUEST_TIMEOUT,
    _current_state,
    _legacy_session,
    _no_matches_response,
    _redirect_for_navigation_outcome,
    _render_filters_page,
    _services,
    _wants_json_response,
    bp,
    logger,
)
from session.user_preferences import set_exclude_watched_default


@bp.route("/next_movie", methods=["POST"])
@csrf_required
@rate_limited("next_movie")
@with_timeout(_REQUEST_TIMEOUT)
async def next_movie():
    services = _services()
    state = _current_state()
    logger.info(
        "Requesting next movie for session_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    services.metrics_collector.track_movie_recommendation("next_movie")
    user_actions_total.labels(action_type="next_movie").inc()

    outcome = await services.movie_manager.next_movie(
        state,
        legacy_session=_legacy_session(),
    )

    if outcome is not None:
        return await _redirect_for_navigation_outcome(outcome)

    logger.warning("No more movies available. Correlation ID: %s", g.correlation_id)
    return "No more movies available. Please try again later.", 200


@bp.route("/previous_movie", methods=["POST"])
@csrf_required
@rate_limited("previous_movie")
@with_timeout(_REQUEST_TIMEOUT)
async def previous_movie():
    movie_manager = _services().movie_manager
    state = _current_state()
    logger.info(
        "Requesting previous movie for session_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )
    outcome = await movie_manager.previous_movie(state, legacy_session=_legacy_session())

    if outcome is None:
        tconst = movie_manager.get_current_movie_tconst(state)
        if tconst:
            return redirect(url_for("main.movie_detail", tconst=tconst))
        return redirect(url_for("main.home"))

    return await _redirect_for_navigation_outcome(outcome)


@bp.route("/filters")
async def set_filters():
    state = _current_state()
    current_filters = state.filters

    start_time = time.time()
    logger.info(
        "Starting to set filters for session_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    response = await _render_filters_page(current_filters)
    elapsed_time = time.time() - start_time
    logger.info(
        "Completed setting filters for session_id: %s in %.2f seconds. Correlation ID: %s",
        state.session_id,
        elapsed_time,
        g.correlation_id,
    )
    return response


@bp.route("/filtered_movie", methods=["POST"])
@csrf_required
@rate_limited("filtered_movie")
@with_timeout(_REQUEST_TIMEOUT)
async def filtered_movie_endpoint():
    movie_manager = _services().movie_manager
    state = _current_state()
    form_data = await request.form
    filters: FilterState = normalize_filters(form_data)
    validation_errors = validate_filters(filters)

    start_time = time.time()
    logger.info(
        "Starting filtering movies for session_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    wants_json = _wants_json_response()

    if validation_errors:
        logger.info(
            "Rejected invalid filters for session_id: %s. Correlation ID: %s. Errors: %s",
            state.session_id,
            g.correlation_id,
            validation_errors,
        )
        elapsed_time = time.time() - start_time
        logger.info(
            "Completed filtering movies for session_id: %s in %.2f seconds. Correlation ID: %s",
            state.session_id,
            elapsed_time,
            g.correlation_id,
        )
        if wants_json:
            return jsonify({"ok": False, "errors": validation_errors}), 400
        return await _render_filters_page(
            filters,
            validation_errors=validation_errors,
            form_notice="Fix the highlighted filters and try again.",
            genres_notice=(
                "No genres selected. Nextreel will use all genres."
                if not filters.get("genres_selected")
                else None
            ),
            status_code=400,
        )

    if state.user_id:
        await set_exclude_watched_default(
            movie_manager.db_pool,
            state.user_id,
            bool(filters["exclude_watched"]),
        )

    outcome = await movie_manager.apply_filters(
        state,
        filters,
        legacy_session=_legacy_session(),
    )
    elapsed_time = time.time() - start_time
    logger.info(
        "Completed filtering movies for session_id: %s in %.2f seconds. Correlation ID: %s",
        state.session_id,
        elapsed_time,
        g.correlation_id,
    )
    if outcome is not None:
        if wants_json:
            if outcome.tconst:
                return jsonify(
                    {
                        "ok": True,
                        "redirect": url_for("main.movie_detail", tconst=outcome.tconst),
                    }
                )
            return _no_matches_response()
        return await _redirect_for_navigation_outcome(outcome)
    if wants_json:
        return _no_matches_response()
    await flash("No movies matched your filters. Try broadening your criteria.", "warning")
    return redirect(url_for("main.set_filters"))


__all__ = [
    "filtered_movie_endpoint",
    "next_movie",
    "previous_movie",
    "set_filters",
]
