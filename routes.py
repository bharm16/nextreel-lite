"""
Application route handlers.

All HTTP endpoints are defined here and registered via a Quart Blueprint.
The ``movie_manager`` and ``metrics_collector`` instances are injected by
``create_app()`` through ``init_routes()``.
"""

import asyncio
import hmac
import hashlib
import os
import re
import secrets
import time
from datetime import datetime

from quart import Blueprint, request, redirect, url_for, session, render_template, g, current_app, abort

# Maximum time (seconds) a route handler will wait for backend operations
_REQUEST_TIMEOUT = 30

# tconst must match IMDb ID format: "tt" followed by digits
_TCONST_RE = re.compile(r"^tt\d{1,10}$")

# CSRF token session key
_CSRF_TOKEN_KEY = "_csrf_token"

from logging_config import get_logger
from metrics_collector import user_actions_total
from session_keys import USER_ID_KEY, CURRENT_FILTERS_KEY

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


def _get_csrf_token() -> str:
    """Return the current CSRF token, generating one if absent."""
    token = session.get(_CSRF_TOKEN_KEY)
    if not token:
        token = secrets.token_hex(32)
        session[_CSRF_TOKEN_KEY] = token
    return token


async def _validate_csrf_from_form() -> None:
    """Validate CSRF token from either header or form body."""
    expected = session.get(_CSRF_TOKEN_KEY)
    if not expected:
        abort(403, "CSRF token missing from session")
    # Check header first
    header_token = request.headers.get("X-CSRFToken")
    if header_token and hmac.compare_digest(header_token, expected):
        return
    # Check form body
    form = await request.form
    form_token = form.get("csrf_token", "")
    if form_token and hmac.compare_digest(form_token, expected):
        return
    abort(403, "CSRF token validation failed")


@bp.app_context_processor
def inject_csrf_token():
    """Make csrf_token() available in all templates."""
    return {"csrf_token": _get_csrf_token}


@bp.route("/logout", methods=["POST"])
async def logout():
    """Securely logout and destroy session."""
    await _validate_csrf_from_form()

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


# Redis-backed rate limiter for ops endpoints.
# Falls back to in-memory if Redis is unavailable.
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 30  # requests per window

# In-memory fallback (single-instance only).
# Cap at 1024 keys to prevent unbounded memory growth from unique IPs.
_RATE_LIMIT_MAX_KEYS = 1024
_rate_limit_store: dict[str, list[float]] = {}

# Ops endpoint auth: set OPS_AUTH_TOKEN env var to require Bearer token on
# /metrics, /ready, and similar internal endpoints.
def _get_ops_auth_token() -> str | None:
    """Read OPS_AUTH_TOKEN lazily so rotation doesn't require a restart."""
    return os.environ.get("OPS_AUTH_TOKEN")


async def _check_rate_limit_redis(endpoint_key: str) -> bool:
    """Rate limit using Redis INCR + EXPIRE (distributed)."""
    try:
        redis_client = current_app.config.get("SESSION_REDIS")
        if not redis_client:
            return _check_rate_limit_memory(endpoint_key)
        ip = request.remote_addr or "unknown"
        key = f"ratelimit:{endpoint_key}:{ip}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, _RATE_LIMIT_WINDOW)
        return count <= _RATE_LIMIT_MAX
    except Exception:
        # Redis unavailable — fall back to in-memory
        return _check_rate_limit_memory(endpoint_key)


def _check_rate_limit_memory(endpoint_key: str) -> bool:
    """In-memory fallback rate limiter (single-instance only)."""
    ip = request.remote_addr or "unknown"
    key = f"{endpoint_key}:{ip}"
    now = time.time()
    timestamps = _rate_limit_store.setdefault(key, [])
    cutoff = now - _RATE_LIMIT_WINDOW
    timestamps[:] = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= _RATE_LIMIT_MAX:
        return False
    timestamps.append(now)

    # Evict stale keys to prevent unbounded memory growth
    if len(_rate_limit_store) > _RATE_LIMIT_MAX_KEYS:
        stale_keys = [
            k for k, v in _rate_limit_store.items()
            if not v or v[-1] < cutoff
        ]
        for k in stale_keys:
            _rate_limit_store.pop(k, None)

    return True


def _check_ops_auth() -> bool:
    """Validate bearer token for ops endpoints. Returns True if allowed."""
    expected = _get_ops_auth_token()
    if not expected:
        return True  # No token configured — allow (dev mode)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return hmac.compare_digest(token, expected)
    return False


@bp.route("/health")
async def health_check():
    """Health check endpoint for load balancers"""
    if not await _check_rate_limit_redis("health"):
        return {"error": "rate limited"}, 429
    return {"status": "healthy"}, 200


@bp.route("/metrics")
async def metrics():
    """Prometheus metrics endpoint (requires OPS_AUTH_TOKEN in production)."""
    if not _check_ops_auth():
        return {"error": "unauthorized"}, 401
    if not await _check_rate_limit_redis("metrics"):
        return {"error": "rate limited"}, 429
    from metrics_collector import metrics_endpoint

    return await metrics_endpoint()


@bp.route("/ready")
async def readiness_check():
    """Readiness check with database connectivity (requires OPS_AUTH_TOKEN in production)."""
    if not _check_ops_auth():
        return {"error": "unauthorized"}, 401
    if not await _check_rate_limit_redis("ready"):
        return {"error": "rate limited"}, 429
    try:
        pool_metrics = await _movie_manager.db_pool.get_metrics()

        if pool_metrics["circuit_breaker_state"] == "open":
            return {"status": "not_ready", "reason": "database_circuit_breaker_open"}, 503

        if pool_metrics["queries_failed"] > 0 and pool_metrics["queries_executed"] > 0:
            failure_rate = pool_metrics["queries_failed"] / pool_metrics["queries_executed"]
            if failure_rate > 0.5:
                return {"status": "not_ready", "reason": "high_db_failure_rate"}, 503

        return {
            "status": "ready",
            "database": {
                "pool_size": pool_metrics["pool_size"],
                "free_connections": pool_metrics["free_connections"],
                "circuit_breaker_state": pool_metrics["circuit_breaker_state"],
                "queries_executed": pool_metrics["queries_executed"],
                "avg_query_time_ms": pool_metrics.get("avg_query_time_ms", 0),
            },
        }, 200

    except Exception as e:
        logger.error("Readiness check failed: %s", e)
        return {"status": "not_ready", "reason": "internal_error"}, 503


@bp.route("/movie/<tconst>")
async def movie_detail(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")

    user_id = session.get(USER_ID_KEY)
    logger.debug(
        "Fetching movie details for tconst: %s, user_id: %s. Correlation ID: %s",
        tconst,
        user_id,
        g.correlation_id,
    )
    try:
        return await asyncio.wait_for(
            _movie_manager.render_movie_by_tconst(
                user_id, tconst, template_name="movie.html"
            ),
            timeout=_REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("Timeout rendering movie %s", tconst)
        return "Request timed out. Please try again.", 504


@bp.route("/")
async def home():
    user_id = session.get(USER_ID_KEY)
    data = await _movie_manager.home(user_id)
    return await render_template(
        "home.html", default_backdrop_url=data["default_backdrop_url"]
    )


@bp.route("/next_movie", methods=["POST"])
async def next_movie():
    await _validate_csrf_from_form()

    if not await _check_rate_limit_redis("next_movie"):
        return {"error": "rate limited"}, 429

    user_id = session.get(USER_ID_KEY)
    logger.info(
        "Requesting next movie for user_id: %s. Correlation ID: %s",
        user_id, g.correlation_id,
    )

    _metrics_collector.track_movie_recommendation("next_movie")
    user_actions_total.labels(action_type="next_movie").inc()

    try:
        response = await asyncio.wait_for(
            _movie_manager.next_movie(user_id), timeout=_REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("Timeout fetching next movie. Correlation ID: %s", g.correlation_id)
        return "Request timed out. Please try again.", 504

    if response:
        return response

    logger.warning("No more movies available. Correlation ID: %s", g.correlation_id)
    return "No more movies available. Please try again later.", 200


@bp.route("/previous_movie", methods=["POST"])
async def previous_movie():
    await _validate_csrf_from_form()

    if not await _check_rate_limit_redis("previous_movie"):
        return {"error": "rate limited"}, 429

    user_id = session.get(USER_ID_KEY)
    logger.info(
        "Requesting previous movie for user_id: %s. Correlation ID: %s",
        user_id, g.correlation_id,
    )
    try:
        response = await asyncio.wait_for(
            _movie_manager.previous_movie(user_id), timeout=_REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("Timeout fetching previous movie. Correlation ID: %s", g.correlation_id)
        return "Request timed out. Please try again.", 504

    if response is None:
        tconst = _movie_manager.get_current_movie_tconst()
        if tconst:
            return redirect(url_for("main.movie_detail", tconst=tconst))
        else:
            return redirect(url_for("main.home"))

    return response


@bp.route("/filters")
async def set_filters():
    user_id = session.get(USER_ID_KEY)
    current_filters = session.get(CURRENT_FILTERS_KEY, {})

    start_time = time.time()
    logger.info(
        "Starting to set filters for user_id: %s. Correlation ID: %s",
        user_id, g.correlation_id,
    )

    try:
        response = await render_template(
            "set_filters.html", current_filters=current_filters,
            current_year=datetime.now().year,
        )
        elapsed_time = time.time() - start_time
        logger.info(
            "Completed setting filters for user_id: %s in %.2f seconds. Correlation ID: %s",
            user_id, elapsed_time, g.correlation_id,
        )
        return response
    except Exception as e:
        logger.error("Error setting filters for user_id: %s, Error: %s", user_id, e)
        raise


_ALLOWED_FILTER_KEYS = frozenset({
    "year_min", "year_max", "imdb_score_min", "imdb_score_max",
    "num_votes_min", "num_votes_max", "genres[]", "language",
})
_MAX_FILTER_VALUE_LEN = 64


@bp.route("/filtered_movie", methods=["POST"])
async def filtered_movie_endpoint():
    await _validate_csrf_from_form()

    if not await _check_rate_limit_redis("filtered_movie"):
        return {"error": "rate limited"}, 429

    user_id = session.get(USER_ID_KEY)
    form_data = await request.form

    # Validate and truncate filter data before storing in session
    safe_filters = {
        k: (v[:_MAX_FILTER_VALUE_LEN] if isinstance(v, str) else v)
        for k, v in form_data.to_dict().items()
        if k in _ALLOWED_FILTER_KEYS
    }
    session[CURRENT_FILTERS_KEY] = safe_filters

    start_time = time.time()
    logger.info(
        "Starting filtering movies for user_id: %s. Correlation ID: %s",
        user_id, g.correlation_id,
    )

    try:
        response = await asyncio.wait_for(
            _movie_manager.filtered_movie(user_id, form_data), timeout=_REQUEST_TIMEOUT,
        )
        elapsed_time = time.time() - start_time
        logger.info(
            "Completed filtering movies for user_id: %s in %.2f seconds. Correlation ID: %s",
            user_id, elapsed_time, g.correlation_id,
        )
        return response
    except asyncio.TimeoutError:
        logger.error("Timeout filtering movies for user_id: %s", user_id)
        return "Request timed out. Please try again.", 504
    except Exception as e:
        logger.error("Error filtering movies for user_id: %s, Error: %s", user_id, e)
        raise
