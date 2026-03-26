import asyncio
import logging
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from quart import Quart, g, request, session
from redis import asyncio as aioredis

import settings
from config.env import get_environment
from infra.cache import SimpleCacheManager
from infra.metrics import MetricsCollector, setup_metrics_middleware
from infra.navigation_state import (
    SESSION_COOKIE_MAX_AGE,
    SESSION_COOKIE_NAME,
    NavigationState,
    default_filter_state,
    utcnow,
)
from infra.secrets import secrets_manager
from logging_config import get_logger, setup_logging
from middleware import add_correlation_id
from movie_service import MovieManager
from routes import bp as routes_bp, init_routes
from scripts.local_env_setup import setup_local_environment

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


setup_logging(log_level=logging.INFO)
logger = get_logger(__name__)

if get_environment() != "production":
    setup_local_environment()


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
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
    )


def create_app():
    if not secrets_manager.validate_all_secrets():
        raise RuntimeError("Failed to validate required secrets. Check logs for details.")

    app = FixedQuart(__name__)
    app.config.from_object(settings.Config())
    app.config["NR_SESSION_COOKIE_NAME"] = SESSION_COOKIE_NAME
    app.config["NR_SESSION_COOKIE_MAX_AGE"] = SESSION_COOKIE_MAX_AGE

    movie_manager = MovieManager(settings.Config.get_db_config())
    app.movie_manager = movie_manager
    app.navigation_state_store = None
    app.shared_redis_pool = None
    app.arq_redis = None
    app.redis_available = False
    app.worker_available = False
    app.secure_cache = None
    app.config["SESSION_REDIS"] = None

    metrics_collector = MetricsCollector(db_pool=movie_manager.db_pool, movie_manager=movie_manager)
    init_routes(movie_manager, metrics_collector)
    movie_manager_started = False
    movie_manager_start_lock = asyncio.Lock()

    async def ensure_movie_manager_started():
        nonlocal movie_manager_started
        if movie_manager_started:
            return

        async with movie_manager_start_lock:
            if movie_manager_started:
                return

            await movie_manager.start()
            app.navigation_state_store = movie_manager.navigation_state_store
            movie_manager_started = True
            logger.info("MovieManager started successfully")

    @app.before_serving
    async def setup_redis():
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
            app.config["SESSION_REDIS"] = redis_client
            app.redis_available = True

            try:
                from session.quart_session_compat import install_session

                install_session(app)
            except Exception as exc:
                logger.warning("Legacy Redis session install failed: %s", exc)

            try:
                app.secure_cache = SimpleCacheManager(connection_pool=shared_pool)
                await app.secure_cache.initialize()
            except Exception as exc:
                logger.warning("Redis cache initialization failed: %s", exc)
                app.secure_cache = None

            if create_arq_pool and RedisSettings:
                try:
                    app.arq_redis = await create_arq_pool(RedisSettings.from_dsn(redis_url))
                    app.worker_available = True
                except Exception as exc:
                    logger.warning("ARQ pool initialization failed: %s", exc)
                    app.arq_redis = None

            logger.info("Redis runtime dependencies initialized")
        except Exception as exc:
            logger.warning("Redis unavailable; continuing in degraded mode: %s", exc)
            app.shared_redis_pool = None
            app.config["SESSION_REDIS"] = None
            app.redis_available = False
            app.worker_available = False
            app.secure_cache = None

    async def enqueue_runtime_job(function_name: str, *args):
        if not app.arq_redis:
            logger.warning(
                "enqueue_runtime_job(%s) skipped: no worker available", function_name
            )
            return None
        return await app.arq_redis.enqueue_job(function_name, *args)

    app.enqueue_runtime_job = enqueue_runtime_job

    @app.before_request
    async def before_request():
        try:
            await add_correlation_id()

            skip_paths = ["/static", "/favicon.ico", "/health", "/ready", "/metrics"]
            if any(request.path.startswith(path) for path in skip_paths):
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
            g.set_nr_sid_cookie = needs_cookie or request.cookies.get(SESSION_COOKIE_NAME) != state.session_id
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

        from infra.security_headers import add_security_headers

        return await add_security_headers(response)

    @asynccontextmanager
    async def lifespan(app: Quart):
        logger.info("Starting application lifecycle")
        try:
            await ensure_movie_manager_started()
            await metrics_collector.start_collection()
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

        try:
            if not await movie_manager.candidate_store.latest_refresh_at():
                if app.worker_available:
                    await app.enqueue_runtime_job("refresh_movie_candidates")
                    logger.info("Enqueued initial movie_candidates refresh")
                else:
                    logger.warning(
                        "movie_candidates is empty and no worker is available to refresh it"
                    )
        except Exception as exc:
            logger.warning("Failed to enqueue initial movie_candidates refresh: %s", exc)

        warm_up_tasks = []
        for _ in range(5):
            warm_up_tasks.append(movie_manager.db_pool.execute("SELECT 1", fetch="one"))
        warm_up_results = await asyncio.gather(*warm_up_tasks, return_exceptions=True)
        warm_up_errors = [r for r in warm_up_results if isinstance(r, Exception)]
        if warm_up_errors:
            logger.warning(
                "Application warm-up: %d/%d queries failed: %s",
                len(warm_up_errors),
                len(warm_up_results),
                warm_up_errors[0],
            )
        logger.info("Application warm-up complete")

    async def _shutdown_resources(app_instance):
        try:
            await asyncio.wait_for(metrics_collector.stop_collection(), timeout=5.0)
            logger.info("Metrics collection stopped")
        except asyncio.TimeoutError:
            logger.warning("Metrics collection stop timed out")
        except Exception as exc:
            logger.warning("Error stopping metrics collection: %s", exc)

        if hasattr(movie_manager, "close"):
            try:
                await asyncio.wait_for(movie_manager.close(), timeout=5.0)
                logger.info("MovieManager closed successfully")
            except Exception as exc:
                logger.warning("Error closing MovieManager: %s", exc)

        if getattr(app_instance, "secure_cache", None):
            try:
                await asyncio.wait_for(app_instance.secure_cache.close(), timeout=3.0)
                logger.info("Secure cache closed")
            except Exception as exc:
                logger.warning("Error closing secure cache: %s", exc)

        if getattr(app_instance, "arq_redis", None):
            try:
                await asyncio.wait_for(app_instance.arq_redis.aclose(), timeout=3.0)
                logger.info("ARQ pool closed")
            except Exception as exc:
                logger.warning("Error closing ARQ pool: %s", exc)

        if getattr(app_instance, "config", {}).get("SESSION_REDIS"):
            try:
                await asyncio.wait_for(app_instance.config["SESSION_REDIS"].aclose(), timeout=3.0)
                logger.info("Session Redis pool closed")
            except Exception as exc:
                logger.warning("Error closing session Redis pool: %s", exc)

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
