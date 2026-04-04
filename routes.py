"""Application route handlers."""

import re
import time
from datetime import datetime, timezone

from quart import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from infra.metrics import user_actions_total
from infra.ops_auth import check_ops_auth
from infra.rate_limit import check_rate_limit, get_rate_limit_backend
from infra.route_helpers import csrf_required, rate_limited, validate_csrf, with_timeout
from logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("main", __name__)
_movie_manager = None
_metrics_collector = None

_REQUEST_TIMEOUT = 30
_TCONST_RE = re.compile(r"^tt\d{1,10}$")


def init_routes(movie_manager, metrics_collector):
    global _movie_manager, _metrics_collector
    _movie_manager = movie_manager
    _metrics_collector = metrics_collector


def _get_manager():
    if _movie_manager is None:
        abort(503, description="Application not fully initialized")
    return _movie_manager


def _legacy_session():
    if current_app.config.get("SESSION_REDIS"):
        return session
    return None


def _current_state():
    state = getattr(g, "navigation_state", None)
    if state is None:
        abort(503, description="Navigation state unavailable")
    return state


def _get_csrf_token() -> str:
    return _current_state().csrf_token


# CSRF validation is canonical in infra.route_helpers.validate_csrf.
# Alias kept for any external callers.
_validate_csrf_from_form = validate_csrf


@bp.app_context_processor
def inject_csrf_token():
    return {"csrf_token": _get_csrf_token}


@bp.route("/logout", methods=["POST"])
@csrf_required
async def logout():
    state = _current_state()
    await _movie_manager.logout(state, legacy_session=_legacy_session())

    response = redirect(url_for("main.home"), code=303)
    response.delete_cookie(
        current_app.config.get("NR_SESSION_COOKIE_NAME", "nr_sid"),
        path="/",
        domain=current_app.config.get("SESSION_COOKIE_DOMAIN"),
    )
    response.delete_cookie(
        current_app.config.get("SESSION_COOKIE_NAME", "session"),
        path="/",
        domain=current_app.config.get("SESSION_COOKIE_DOMAIN"),
    )
    return response


@bp.route("/health")
async def health_check():
    if not await check_rate_limit("health"):
        return {"error": "rate limited"}, 429
    return {"status": "healthy"}, 200


@bp.route("/metrics")
async def metrics():
    if not check_ops_auth():
        return {"error": "unauthorized"}, 401
    if not await check_rate_limit("metrics"):
        return {"error": "rate limited"}, 429
    from infra.metrics import metrics_endpoint

    return await metrics_endpoint()


@bp.route("/ready")
async def readiness_check():
    if not check_ops_auth():
        return {"error": "unauthorized"}, 401
    if not await check_rate_limit("ready"):
        return {"error": "rate limited"}, 429

    try:
        pool_metrics = await _movie_manager.db_pool.get_metrics()
        if pool_metrics["circuit_breaker_state"] == "open":
            return {"status": "not_ready", "reason": "database_circuit_breaker_open"}, 503

        navigation_ready = await current_app.navigation_state_store.ready_check()
        candidates_fresh = await _movie_manager.candidate_store.has_fresh_data()
        projection_ready = await _movie_manager.projection_store.ready_check()
        status_code = 200 if navigation_ready and candidates_fresh and projection_ready else 503

        return {
            "status": "ready" if status_code == 200 else "not_ready",
            "database": {
                "pool_size": pool_metrics["pool_size"],
                "free_connections": pool_metrics["free_connections"],
                "circuit_breaker_state": pool_metrics["circuit_breaker_state"],
                "queries_executed": pool_metrics["queries_executed"],
                "avg_query_time_ms": pool_metrics.get("avg_query_time_ms", 0),
            },
            "navigation_state": {
                "ready": navigation_ready,
            },
            "movie_candidates": {
                "fresh": candidates_fresh,
            },
            "projection_generation": {
                "ready": projection_ready,
            },
            "degraded": {
                "redis": "available" if current_app.redis_available else "unavailable",
                "worker": "available" if current_app.worker_available else "unavailable",
                "rate_limiter_backend": get_rate_limit_backend(),
            },
        }, status_code
    except Exception as exc:
        logger.error("Readiness check failed: %s", exc)
        return {"status": "not_ready", "reason": "internal_error"}, 503


@bp.route("/movie/<tconst>")
@with_timeout(_REQUEST_TIMEOUT)
async def movie_detail(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")

    state = _current_state()
    logger.debug(
        "Fetching movie details for tconst: %s, state_id: %s. Correlation ID: %s",
        tconst,
        state.session_id,
        g.correlation_id,
    )
    return await _movie_manager.render_movie_by_tconst(state, tconst, template_name="movie.html")


@bp.route("/")
async def home():
    state = _current_state()
    data = await _movie_manager.home(state, legacy_session=_legacy_session())
    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
    )


@bp.route("/next_movie", methods=["POST"])
@csrf_required
@rate_limited("next_movie")
@with_timeout(_REQUEST_TIMEOUT)
async def next_movie():
    state = _current_state()
    logger.info(
        "Requesting next movie for state_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    _metrics_collector.track_movie_recommendation("next_movie")
    user_actions_total.labels(action_type="next_movie").inc()

    response = await _movie_manager.next_movie(state, legacy_session=_legacy_session())

    if response:
        return response

    logger.warning("No more movies available. Correlation ID: %s", g.correlation_id)
    return "No more movies available. Please try again later.", 200


@bp.route("/previous_movie", methods=["POST"])
@csrf_required
@rate_limited("previous_movie")
@with_timeout(_REQUEST_TIMEOUT)
async def previous_movie():
    state = _current_state()
    logger.info(
        "Requesting previous movie for state_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )
    response = await _movie_manager.previous_movie(state, legacy_session=_legacy_session())

    if response is None:
        tconst = _movie_manager.get_current_movie_tconst(state)
        if tconst:
            return redirect(url_for("main.movie_detail", tconst=tconst))
        return redirect(url_for("main.home"))

    return response


@bp.route("/filters")
async def set_filters():
    state = _current_state()
    current_filters = state.filters

    start_time = time.time()
    logger.info(
        "Starting to set filters for state_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    response = await render_template(
        "set_filters.html",
        current_filters=current_filters,
        current_year=datetime.now(timezone.utc).year,
    )
    elapsed_time = time.time() - start_time
    logger.info(
        "Completed setting filters for state_id: %s in %.2f seconds. Correlation ID: %s",
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
    state = _current_state()
    form_data = await request.form

    start_time = time.time()
    logger.info(
        "Starting filtering movies for state_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    response = await _movie_manager.filtered_movie(state, form_data, legacy_session=_legacy_session())
    elapsed_time = time.time() - start_time
    logger.info(
        "Completed filtering movies for state_id: %s in %.2f seconds. Correlation ID: %s",
        state.session_id,
        elapsed_time,
        g.correlation_id,
    )
    if response:
        return response
    await flash("No movies matched your filters. Try broadening your criteria.", "warning")
    return redirect(url_for("main.set_filters"))
