import asyncio
import logging
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from uuid import uuid4

# --- Early local-env bootstrap -------------------------------------------
# Must run BEFORE ``import settings`` so that ``get_environment()``'s first
# (cached) call sees NEXTREEL_ENV=development when no env var is set in the
# shell.  In production the env var is always set by the deploy pipeline, so
# this block is a no-op there.
if not os.environ.get("NEXTREEL_ENV") and not os.environ.get("FLASK_ENV"):
    from scripts.local_env_setup import setup_local_environment

    setup_local_environment()
# -------------------------------------------------------------------------

from quart import Quart, g, request, session
from redis import asyncio as aioredis

import settings
from env_bootstrap import get_environment
from infra.cache import SimpleCacheManager
from infra.metrics import MetricsCollector, setup_metrics_middleware
from infra.navigation_state import (
    SESSION_COOKIE_MAX_AGE,
    SESSION_COOKIE_NAME,
    NavigationState,
    default_filter_state,
    utcnow,
)
from infra.runtime_schema import ensure_movie_candidates_fulltext_index
from infra.secrets import secrets_manager
from infra.security_headers import add_security_headers
from logging_config import get_logger, setup_logging
from middleware import add_correlation_id
from movie_service import MovieManager
from routes import bp as routes_bp, init_routes

try:
    from arq import create_pool as create_arq_pool
    from arq.connections import RedisSettings
except ImportError:  # pragma: no cover - optional dependency at import time
    create_arq_pool = None
    RedisSettings = None


class FixedQuart(Quart):
    """Quart subclass ensuring Flask compatibility keys."""

    default_config = dict(Quart.default_config)
    default_config.setdefault("PROVIDE_AUTOMATIC_OPTIONS", True)


logger = get_logger(__name__)

_SKIP_PATHS = ("/static", "/favicon.ico", "/health", "/ready", "/metrics")


def _redis_url() -> str:
    if get_environment() == "production":
        redis_host = os.getenv("UPSTASH_REDIS_HOST")
        redis_port = os.getenv("UPSTASH_REDIS_PORT")
        redis_pw = os.getenv("UPSTASH_REDIS_PASSWORD")
        if not redis_host or not redis_port:
            raise RuntimeError(
                "UPSTASH_REDIS_HOST and UPSTASH_REDIS_PORT must be set in production"
            )
        return f"rediss://:{redis_pw}@{redis_host}:{redis_port}"
    return os.getenv("REDIS_URL", "redis://localhost:6379")


def _build_test_navigation_state() -> NavigationState:
    now = utcnow()
    return NavigationState(
        session_id=uuid4().hex,
        version=1,
        csrf_token="test-csrf-token",
        filters=default_filter_state(),
        current_tconst=None,
        current_ref=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
    )


async def _setup_redis(app):
    """Initialize Redis runtime dependencies (session, cache, ARQ)."""
    redis_url = _redis_url()
    try:
        shared_pool = aioredis.ConnectionPool.from_url(
            redis_url,
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", 30)),
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        redis_client = aioredis.Redis(connection_pool=shared_pool)
        await redis_client.ping()
        app.shared_redis_pool = shared_pool
        app.redis_url = redis_url
        app.config["SESSION_REDIS"] = redis_client
        app.redis_available = True

        try:
            from session.quart_session_compat import install_session

            install_session(app)
        except Exception as exc:
            logger.warning("Legacy Redis session install failed: %s", exc)

        try:
            app.redis_cache = SimpleCacheManager.from_client(
                redis_client,
                verify_connection=False,
            )
            app.secure_cache = app.redis_cache
            await app.redis_cache.initialize()
            try:
                app.movie_manager.attach_cache(app.redis_cache)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to attach cache to movie_manager: %s", exc)
        except Exception as exc:
            logger.warning("Redis cache initialization failed: %s", exc)
            app.redis_cache = None
            app.secure_cache = None

        logger.info("Redis runtime dependencies initialized")
    except Exception as exc:
        logger.warning("Redis unavailable; continuing in degraded mode: %s", exc)
        app.shared_redis_pool = None
        app.redis_url = redis_url
        app.config["SESSION_REDIS"] = None
        app.redis_available = False
        app.worker_available = False
        app.redis_cache = None
        app.secure_cache = None


async def _shutdown_resources(app):
    """Gracefully shut down all runtime resources."""
    try:
        await asyncio.wait_for(app.metrics_collector.stop_collection(), timeout=5.0)
        logger.info("Metrics collection stopped")
    except asyncio.TimeoutError:
        logger.warning("Metrics collection stop timed out")
    except Exception as exc:
        logger.warning("Error stopping metrics collection: %s", exc)

    if hasattr(app.movie_manager, "close"):
        try:
            await asyncio.wait_for(app.movie_manager.close(), timeout=5.0)
            logger.info("MovieManager closed successfully")
        except Exception as exc:
            logger.warning("Error closing MovieManager: %s", exc)

    if getattr(app, "redis_cache", None):
        try:
            await asyncio.wait_for(app.redis_cache.close(), timeout=3.0)
            logger.info("Secure cache closed")
        except Exception as exc:
            logger.warning("Error closing secure cache: %s", exc)

    if getattr(app, "arq_redis", None):
        try:
            await asyncio.wait_for(app.arq_redis.aclose(), timeout=3.0)
            logger.info("ARQ pool closed")
        except Exception as exc:
            logger.warning("Error closing ARQ pool: %s", exc)

    if getattr(app, "config", {}).get("SESSION_REDIS"):
        try:
            await asyncio.wait_for(app.config["SESSION_REDIS"].aclose(), timeout=3.0)
            logger.info("Session Redis pool closed")
        except Exception as exc:
            logger.warning("Error closing session Redis pool: %s", exc)


def _init_core(app):
    """Phase 1: Core app config and movie manager."""
    app.config.from_object(settings.Config())
    app.config["NR_SESSION_COOKIE_NAME"] = SESSION_COOKIE_NAME
    app.config["NR_SESSION_COOKIE_MAX_AGE"] = SESSION_COOKIE_MAX_AGE

    # CSS cache-busting: use output.css mtime as version query param
    import os as _os

    css_path = _os.path.join(app.root_path, "static", "css", "output.css")
    app.config["CSS_VERSION"] = (
        str(int(_os.path.getmtime(css_path))) if _os.path.exists(css_path) else "1"
    )

    movie_manager = MovieManager(settings.Config.get_db_config())
    app.movie_manager = movie_manager
    app.navigation_state_store = None
    app.shared_redis_pool = None
    app.arq_redis = None
    app.redis_url = None
    app.redis_available = False
    app.worker_available = False
    app.redis_cache = None
    app.secure_cache = None
    app.background_tasks = set()
    app.config["SESSION_REDIS"] = None
    return movie_manager


def _init_oauth(app):
    """Phase 1b: OAuth client setup (optional — skipped if no credentials configured)."""
    import os as _os

    google_client_id = _os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret = _os.getenv("GOOGLE_CLIENT_SECRET")
    apple_client_id = _os.getenv("APPLE_CLIENT_ID")
    redirect_base = _os.getenv("OAUTH_REDIRECT_BASE_URL", "http://127.0.0.1:5000")

    app.oauth_config = {
        "google_enabled": bool(google_client_id and google_client_secret),
        "apple_enabled": bool(apple_client_id),
        "google_client_id": google_client_id,
        "google_client_secret": google_client_secret,
        "apple_client_id": apple_client_id,
        "apple_team_id": _os.getenv("APPLE_TEAM_ID"),
        "apple_key_id": _os.getenv("APPLE_KEY_ID"),
        "apple_private_key": _os.getenv("APPLE_PRIVATE_KEY"),
        "redirect_base": redirect_base,
    }


def _init_metrics(app, movie_manager):
    """Phase 2: Metrics collector and route wiring."""
    metrics_collector = MetricsCollector(db_pool=movie_manager.db_pool, movie_manager=movie_manager)
    app.metrics_collector = metrics_collector
    init_routes(app, movie_manager, metrics_collector)
    return metrics_collector


def _make_manager_starter(app, movie_manager):
    """Phase 3: Lazy MovieManager startup guard."""
    started = False
    lock = asyncio.Lock()

    async def ensure_movie_manager_started():
        nonlocal started
        if started:
            return

        async with lock:
            if started:
                return

            await movie_manager.start()
            app.navigation_state_store = movie_manager.navigation_state_store
            started = True
            logger.info("MovieManager started successfully")

    return ensure_movie_manager_started


def create_app():
    setup_logging(log_level=logging.INFO)
    if not secrets_manager.validate_all_secrets():
        raise RuntimeError("Failed to validate required secrets. Check logs for details.")

    app = FixedQuart(__name__)
    movie_manager = _init_core(app)
    _init_oauth(app)
    metrics_collector = _init_metrics(app, movie_manager)
    ensure_movie_manager_started = _make_manager_starter(app, movie_manager)

    @app.before_serving
    async def setup_redis():
        await _setup_redis(app)

    arq_lock = asyncio.Lock()

    async def ensure_arq_pool():
        if app.arq_redis:
            return app.arq_redis
        if not app.redis_available or not app.redis_url or not create_arq_pool or not RedisSettings:
            return None

        async with arq_lock:
            if app.arq_redis:
                return app.arq_redis
            try:
                app.arq_redis = await create_arq_pool(RedisSettings.from_dsn(app.redis_url))
                app.worker_available = True
                logger.info("ARQ pool initialized lazily")
            except Exception as exc:
                app.worker_available = False
                logger.warning("ARQ pool initialization failed: %s", exc)
                app.arq_redis = None
            return app.arq_redis

    async def enqueue_runtime_job(function_name: str, *args):
        pool = await ensure_arq_pool()
        if not pool:
            return None
        return await pool.enqueue_job(function_name, *args)

    app.enqueue_runtime_job = enqueue_runtime_job
    movie_manager.projection_store.enqueue_fn = enqueue_runtime_job

    @app.before_request
    async def before_request():
        try:
            await add_correlation_id()

            if any(request.path.startswith(p) for p in _SKIP_PATHS):
                return

            if app.config.get("TESTING") and app.navigation_state_store is None:
                g.navigation_state = _build_test_navigation_state()
                g.set_nr_sid_cookie = False
                return

            await ensure_movie_manager_started()

            legacy_session = session if app.config.get("SESSION_REDIS") else None
            state, needs_cookie = await app.navigation_state_store.load_for_request(
                request.cookies.get(SESSION_COOKIE_NAME),
                legacy_session=legacy_session,
            )
            g.navigation_state = state
            g.set_nr_sid_cookie = (
                needs_cookie or request.cookies.get(SESSION_COOKIE_NAME) != state.session_id
            )
        except (asyncio.CancelledError, SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            logger.error("Error loading navigation state: %s", exc, exc_info=True)
            from quart import make_response

            return await make_response(("Service temporarily unavailable", 503))

    @app.after_request
    async def after_request(response):
        if hasattr(g, "start_time"):
            elapsed = time.time() - g.start_time
            if elapsed > 1.0:
                logger.warning(
                    "Slow request: %s took %.2fs (state: %s, correlation: %s)",
                    request.endpoint,
                    elapsed,
                    getattr(getattr(g, "navigation_state", None), "session_id", None),
                    g.get("correlation_id"),
                )
            response.headers["X-Response-Time"] = f"{elapsed:.3f}"

        state = getattr(g, "navigation_state", None)
        if state and getattr(g, "set_nr_sid_cookie", False):
            response.set_cookie(
                SESSION_COOKIE_NAME,
                state.session_id,
                max_age=SESSION_COOKIE_MAX_AGE,
                secure=app.config.get("SESSION_COOKIE_SECURE", False),
                httponly=True,
                samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
                domain=app.config.get("SESSION_COOKIE_DOMAIN"),
                path="/",
            )

        return await add_security_headers(response)

    @asynccontextmanager
    async def lifespan(app: Quart):
        logger.info("Starting application lifecycle")
        try:
            await ensure_movie_manager_started()
            await app.metrics_collector.start_collection()
            logger.info("Metrics collection started")
        except Exception as exc:
            logger.error("Failed to start MovieManager: %s", exc)
            raise

        yield

        logger.info("Shutting down application lifecycle")
        try:
            await _shutdown_resources(app)
        except Exception as exc:
            logger.error("Critical error during shutdown: %s", exc)

    app.lifespan = lifespan

    setup_metrics_middleware(app, metrics_collector)

    @app.before_serving
    async def startup():
        logger.info("Starting application warm-up...")
        await ensure_movie_manager_started()

        async def repair_fulltext_index():
            try:
                await ensure_movie_candidates_fulltext_index(movie_manager.db_pool)
            except Exception as exc:
                logger.warning("Failed to repair movie_candidates FULLTEXT index: %s", exc)

        task = asyncio.create_task(repair_fulltext_index())
        app.background_tasks.add(task)
        task.add_done_callback(app.background_tasks.discard)

        try:
            if not await movie_manager.candidate_store.latest_refresh_at():
                job = await app.enqueue_runtime_job("refresh_movie_candidates")
                if job is not None:
                    logger.info("Enqueued initial movie_candidates refresh")
                else:
                    logger.warning(
                        "movie_candidates is empty and no worker is available to refresh it"
                    )
        except Exception as exc:
            logger.warning("Failed to enqueue initial movie_candidates refresh: %s", exc)

        logger.info("Application warm-up complete")

    app.register_blueprint(routes_bp)
    return app


def find_free_port(start_port=5000, host="127.0.0.1"):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((host, port)) != 0:
                return port
            port += 1


if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", find_free_port()))
    logger.info("Starting development server on http://127.0.0.1:%s", port)
    app.run(host="127.0.0.1", port=port)
