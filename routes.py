"""
Application route handlers.

All HTTP endpoints are defined here and registered via a Quart Blueprint.
The ``movie_manager`` and ``metrics_collector`` instances are injected by
``create_app()`` through ``init_routes()``.
"""

import time
import uuid

from quart import Blueprint, request, redirect, url_for, session, render_template, g

from logging_config import get_logger
from metrics_collector import (
    movie_recommendations_total,
    user_sessions_total,
    user_actions_total,
)

logger = get_logger(__name__)

bp = Blueprint("main", __name__)

# These are set by init_routes() from create_app()
_movie_manager = None
_metrics_collector = None


def init_routes(movie_manager, metrics_collector):
    """Inject dependencies into the routes module."""
    global _movie_manager, _metrics_collector
    _movie_manager = movie_manager
    _metrics_collector = metrics_collector


@bp.route("/logout", methods=["POST"])
async def logout():
    """Securely logout and destroy session."""
    from session_security_enhanced import EnhancedSessionSecurity
    from quart import current_app

    session_security = current_app.config.get("_session_security")
    if session_security:
        await session_security.destroy_session()
    else:
        session.clear()

    response = redirect(url_for("main.home"))
    response.set_cookie(
        current_app.config["SESSION_COOKIE_NAME"],
        "",
        expires=0,
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
        httponly=True,
        samesite="Lax",
    )
    return response


@bp.route("/health")
async def health_check():
    """Health check endpoint for load balancers"""
    return {"status": "healthy"}, 200


@bp.route("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    from metrics_collector import metrics_endpoint

    return await metrics_endpoint()


@bp.route("/ready")
async def readiness_check():
    """Readiness check with database connectivity"""
    try:
        metrics = await _movie_manager.db_pool.get_metrics()

        if metrics["circuit_breaker_state"] == "open":
            return {"status": "not_ready", "reason": "database_circuit_breaker_open"}, 503

        if metrics["queries_failed"] > 0 and metrics["queries_executed"] > 0:
            failure_rate = metrics["queries_failed"] / metrics["queries_executed"]
            if failure_rate > 0.5:
                return {"status": "not_ready", "reason": "high_db_failure_rate"}, 503

        return {
            "status": "ready",
            "database": {
                "pool_size": metrics["pool_size"],
                "free_connections": metrics["free_connections"],
                "circuit_breaker_state": metrics["circuit_breaker_state"],
                "queries_executed": metrics["queries_executed"],
                "avg_query_time_ms": metrics.get("avg_query_time_ms", 0),
            },
        }, 200

    except Exception as e:
        return {"status": "not_ready", "reason": str(e)}, 503


@bp.route("/movie/<tconst>")
async def movie_detail(tconst):
    user_id = session.get("user_id")
    logger.debug(
        "Fetching movie details for tconst: %s, user_id: %s. Correlation ID: %s",
        tconst,
        user_id,
        g.correlation_id,
    )
    return await _movie_manager.render_movie_by_tconst(
        user_id, tconst, template_name="movie.html"
    )


@bp.route("/")
async def home():
    user_id = session.get("user_id")
    return await _movie_manager.home(user_id)


@bp.route("/next_movie", methods=["GET", "POST"])
async def next_movie():
    user_id = session.get("user_id")
    logger.info(
        f"Requesting next movie for user_id: {user_id}. Correlation ID: {g.correlation_id}"
    )

    _metrics_collector.track_movie_recommendation("next_movie")
    user_actions_total.labels(action_type="next_movie").inc()

    response = await _movie_manager.next_movie(user_id)
    if response:
        return response

    logger.warning(f"No more movies available. Correlation ID: {g.correlation_id}")
    return "No more movies available. Please try again later.", 200


@bp.route("/previous_movie", methods=["GET", "POST"])
async def previous_movie():
    user_id = session.get("user_id")
    logger.info(
        f"Requesting previous movie for user_id: {user_id}. Correlation ID: {g.correlation_id}"
    )
    response = await _movie_manager.previous_movie(user_id)

    if response is None:
        current_movie = session.get("current_movie")
        if current_movie and current_movie.get("imdb_id"):
            return redirect(
                url_for("main.movie_detail", tconst=current_movie.get("imdb_id"))
            )
        else:
            return redirect(url_for("main.home"))

    return response


@bp.route("/setFilters")
async def set_filters():
    user_id = session.get("user_id")
    current_filters = session.get("current_filters", {})

    start_time = time.time()
    logger.info(
        f"Starting to set filters for user_id: {user_id} with current filters: {current_filters}. Correlation ID: {g.correlation_id}"
    )

    try:
        response = await render_template(
            "set_filters.html", current_filters=current_filters
        )
        elapsed_time = time.time() - start_time
        logger.info(
            f"Completed setting filters for user_id: {user_id} in {elapsed_time:.2f} seconds. Correlation ID: {g.correlation_id}"
        )
        return response
    except Exception as e:
        logger.error(f"Error setting filters for user_id: {user_id}, Error: {e}")
        raise


@bp.route("/filtered_movie", methods=["POST"])
async def filtered_movie_endpoint():
    user_id = session.get("user_id")
    form_data = await request.form

    session["current_filters"] = form_data.to_dict()

    start_time = time.time()
    logger.info(
        f"Starting filtering movies for user_id: {user_id} with form data: {form_data}. Correlation ID: {g.correlation_id}"
    )

    try:
        response = await _movie_manager.filtered_movie(user_id, form_data)
        elapsed_time = time.time() - start_time
        logger.info(
            f"Completed filtering movies for user_id: {user_id} in {elapsed_time:.2f} seconds. Correlation ID: {g.correlation_id}"
        )
        return response
    except Exception as e:
        logger.error(f"Error filtering movies for user_id: {user_id}, Error: {e}")
        raise


@bp.route("/handle_new_user")
async def handle_new_user():
    user_id = session.get("user_id", str(uuid.uuid4()))
    session["user_id"] = user_id
    criteria = {
        "min_year": 1900,
        "max_year": 2023,
        "min_rating": 7.0,
        "genres": ["Action", "Comedy"],
    }

    await _movie_manager.add_user(user_id, criteria)
    logger.info(
        f"New user handled with user_id: {user_id}. Correlation ID: {g.correlation_id}"
    )

    return redirect(url_for("main.home"))
