from __future__ import annotations

import os

from redis import asyncio as aioredis

from env_bootstrap import get_environment
from infra.cache import SimpleCacheManager
from infra.time_utils import env_int
from logging_config import get_logger

logger = get_logger(__name__)


def resolve_redis_url(*, environment: str | None = None, environ=os.environ) -> str:
    env = environment or get_environment()
    if env == "production":
        redis_url = environ.get("REDIS_URL")
        if redis_url:
            return redis_url
        redis_host = environ.get("UPSTASH_REDIS_HOST")
        redis_port = environ.get("UPSTASH_REDIS_PORT")
        redis_pw = environ.get("UPSTASH_REDIS_PASSWORD")
        if not redis_host or not redis_port:
            raise RuntimeError(
                "REDIS_URL or UPSTASH_REDIS_HOST/UPSTASH_REDIS_PORT must be set in production"
            )
        return f"rediss://:{redis_pw}@{redis_host}:{redis_port}"
    return environ.get("REDIS_URL", "redis://localhost:6379")


async def setup_redis_runtime(
    app,
    *,
    redis_url: str | None = None,
    redis_module=aioredis,
    cache_manager_cls=SimpleCacheManager,
    install_session_fn=None,
) -> None:
    """Initialize Redis-backed session, cache, and runtime availability flags."""
    resolved_url = redis_url or resolve_redis_url()
    try:
        shared_pool = redis_module.ConnectionPool.from_url(
            resolved_url,
            max_connections=env_int("REDIS_MAX_CONNECTIONS", 30),
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        redis_client = redis_module.Redis(connection_pool=shared_pool)
        await redis_client.ping()
        app.shared_redis_pool = shared_pool
        app.redis_url = resolved_url
        app.config["SESSION_REDIS"] = redis_client
        app.redis_available = True

        if install_session_fn is None:
            try:
                from session.quart_session_compat import install_session

                install_session_fn = install_session
            except Exception as exc:
                logger.warning("Legacy Redis session install failed: %s", exc)
                install_session_fn = None
        if install_session_fn is not None:
            install_session_fn(app)

        try:
            app.redis_cache = cache_manager_cls.from_client(
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
        app.redis_url = resolved_url
        app.config["SESSION_REDIS"] = None
        app.redis_available = False
        app.worker_available = False
        app.redis_cache = None
        app.secure_cache = None
