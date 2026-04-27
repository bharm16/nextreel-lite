"""Navigation and filtering route handlers."""

from __future__ import annotations

import time

from quart import g, jsonify, redirect, request, url_for

from infra.event_schema import (
    EVENT_FILTER_APPLIED,
    EVENT_MOVIE_SWIPED,
)
from infra.events import track_event
from infra.metrics import movie_filters_applied_total, user_actions_total
from infra.filter_normalizer import default_filter_state, normalize_filters, validate_filters
from infra.route_helpers import csrf_required, rate_limited, with_timeout
from infra.time_utils import current_year
from nextreel.domain.filter_contracts import FilterState
from nextreel.web.routes.shared import (
    _REQUEST_TIMEOUT,
    _build_movie_url_for_tconst,
    _build_movie_url_from_outcome,
    _current_state,
    _distinct_id_for,
    _legacy_session,
    _no_matches_response,
    _redirect_for_navigation_outcome,
    _services,
    _wants_json_response,
    bp,
    logger,
)
from session.user_preferences import (
    set_exclude_watched_default,
    set_exclude_watchlist_default,
)


def _active_filter_dimensions(filters: FilterState) -> list[str]:
    """Return the closed-set list of filter dimensions active in this request.

    A dimension is "active" when narrowed from defaults or set to True for
    the boolean exclude flags. Used both for the Prometheus
    ``movie_filters_applied_total`` counter and the PostHog
    ``filter_applied`` event payload.
    """
    defaults = default_filter_state(current_year())
    dimensions: list[str] = []
    if filters.get("genres_selected"):
        dimensions.append("genres")
    if (
        filters.get("year_min") != defaults["year_min"]
        or filters.get("year_max") != defaults["year_max"]
    ):
        dimensions.append("year")
    if (
        filters.get("imdb_score_min") != defaults["imdb_score_min"]
        or filters.get("imdb_score_max") != defaults["imdb_score_max"]
    ):
        dimensions.append("rating")
    if (
        filters.get("num_votes_min") != defaults["num_votes_min"]
        or filters.get("num_votes_max") != defaults["num_votes_max"]
    ):
        dimensions.append("votes")
    language = filters.get("language")
    if language and language != defaults["language"]:
        dimensions.append("language")
    if filters.get("exclude_watched"):
        dimensions.append("exclude_watched")
    if filters.get("exclude_watchlist"):
        dimensions.append("exclude_watchlist")
    return dimensions


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
    track_event(_distinct_id_for(state), EVENT_MOVIE_SWIPED, {"direction": "next"})

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
    user_actions_total.labels(action_type="previous_movie").inc()
    track_event(_distinct_id_for(state), EVENT_MOVIE_SWIPED, {"direction": "previous"})
    outcome = await movie_manager.previous_movie(state, legacy_session=_legacy_session())

    if outcome is None:
        tconst = movie_manager.get_current_movie_tconst(state)
        if tconst:
            url = await _build_movie_url_for_tconst(tconst)
            return redirect(url)
        return redirect(url_for("main.home"))

    return await _redirect_for_navigation_outcome(outcome)


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

    user_actions_total.labels(action_type="filtered_movie").inc()
    if not validation_errors:
        active_dimensions = _active_filter_dimensions(filters)
        for dimension in active_dimensions:
            movie_filters_applied_total.labels(filter_type=dimension).inc()
        track_event(
            _distinct_id_for(state),
            EVENT_FILTER_APPLIED,
            {"dimensions": active_dimensions},
        )

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
        return jsonify({"ok": False, "errors": validation_errors}), 400

    if state.user_id:
        await set_exclude_watched_default(
            movie_manager.db_pool,
            state.user_id,
            bool(filters["exclude_watched"]),
        )
        await set_exclude_watchlist_default(
            movie_manager.db_pool,
            state.user_id,
            bool(filters["exclude_watchlist"]),
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
                url = await _build_movie_url_from_outcome(outcome)
                return jsonify({"ok": True, "redirect": url})
            return _no_matches_response()
        return await _redirect_for_navigation_outcome(outcome)
    if wants_json:
        return _no_matches_response()
    tconst = movie_manager.get_current_movie_tconst(state)
    if tconst:
        url = await _build_movie_url_for_tconst(tconst)
        return redirect(url, code=303)
    return redirect(url_for("main.home"), code=303)


__all__ = [
    "filtered_movie_endpoint",
    "next_movie",
    "previous_movie",
]
