from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from quart import Quart

from infra.runtime_schema import ensure_movie_candidates_fulltext_index
from logging_config import get_logger

logger = get_logger(__name__)


async def shutdown_resources(app) -> None:
    """Gracefully shut down runtime resources owned by the Quart app."""
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


def register_lifecycle_handlers(
    app,
    *,
    ensure_movie_manager_started,
    movie_manager,
    shutdown_fn=shutdown_resources,
) -> None:
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
            await shutdown_fn(app)
        except Exception as exc:
            logger.error("Critical error during shutdown: %s", exc)

    app.lifespan = lifespan

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
