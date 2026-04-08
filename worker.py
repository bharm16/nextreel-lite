"""ARQ worker entrypoint for runtime maintenance jobs."""

from __future__ import annotations

import asyncio
import os

from infra.integrity_checks import INTEGRITY_CHECKS
from infra.pool import DatabaseConnectionPool
from infra.worker_metrics import (
    instrument_job,
    resolve_queue_key,
    run_queue_poller,
    start_worker_metrics_server,
)
from logging_config import get_logger
from movies.candidate_store import CandidateStore
from movies.projection_store import ProjectionStore
from settings import Config

logger = get_logger(__name__)

# Brief pause between full-batch DELETEs in purge_expired_navigation_state to
# avoid saturating MySQL/replication when chewing through a backlog.
PURGE_BATCH_SLEEP_SECONDS = 0.1

# Queue name for the isolated maintenance worker. Defined outside the
# `if RedisSettings is not None:` block so callers can import this constant
# even when arq is not installed.
MAINTENANCE_QUEUE_NAME = "nextreel_maintenance"

try:
    from arq.connections import RedisSettings
    from arq import cron
except ImportError:  # pragma: no cover - exercised only when arq is missing
    RedisSettings = None
    cron = None


async def _maybe_start_queue_poller(ctx, worker_settings_cls=None):
    """Start the ARQ queue-depth poller if a Redis client is reachable."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return
    try:
        from redis import asyncio as aioredis

        redis_client = aioredis.Redis.from_url(redis_url)
        await redis_client.ping()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Worker queue poller: Redis unavailable (%s)", exc)
        return

    queue_key = resolve_queue_key(worker_settings_cls)
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        run_queue_poller(redis_client, queue_key, stop_event=stop_event)
    )
    ctx["_queue_poller_task"] = task
    ctx["_queue_poller_stop"] = stop_event
    ctx["_queue_poller_redis"] = redis_client


async def startup(ctx, *, worker_settings_cls=None, metrics_port_default=None):
    db_pool = DatabaseConnectionPool(Config.get_db_config())
    await db_pool.init_pool()
    ctx["db_pool"] = db_pool
    ctx["candidate_store"] = CandidateStore(db_pool)
    ctx["projection_store"] = ProjectionStore(db_pool)
    ctx["projection_coordinator"] = ctx["projection_store"].coordinator

    # Optional Redis cache for cross-job invalidation (e.g. count-cache
    # generation bump after refresh_movie_candidates).
    try:
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            from infra.cache import SimpleCacheManager

            cache = SimpleCacheManager.from_url(redis_url)
            await cache.initialize()
            ctx["redis_cache"] = cache
        else:
            ctx["redis_cache"] = None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Worker Redis cache unavailable: %s", exc)
        ctx["redis_cache"] = None

    # Prometheus metrics endpoint. Second-bind failures are logged and ignored
    # so a colocated hot-path + maintenance worker pair can't crash each other.
    try:
        start_worker_metrics_server(port=metrics_port_default)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Worker metrics server start failed: %s", exc)

    # Best-effort queue depth poller.
    try:
        await _maybe_start_queue_poller(ctx, worker_settings_cls)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Worker queue poller start failed: %s", exc)

    logger.info("Worker context initialized")


async def startup_hot_path(ctx):
    """arq on_startup for WorkerSettings (hot path worker)."""
    import worker as _self  # noqa: WPS433 — avoid circular class ref

    await startup(
        ctx,
        worker_settings_cls=getattr(_self, "WorkerSettings", None),
    )


async def startup_maintenance(ctx):
    """arq on_startup for MaintenanceWorkerSettings.

    Uses a different default metrics port so both workers can run on the
    same host without colliding.
    """
    import worker as _self  # noqa: WPS433

    default_port = None
    # Only override the default when the caller did not set WORKER_METRICS_PORT
    # explicitly — otherwise ops intent wins.
    if not os.getenv("WORKER_METRICS_PORT"):
        default_port = 8002
    await startup(
        ctx,
        worker_settings_cls=getattr(_self, "MaintenanceWorkerSettings", None),
        metrics_port_default=default_port,
    )


async def shutdown(ctx):
    # Stop the queue poller first so it releases its Redis client.
    stop_event = ctx.get("_queue_poller_stop")
    task = ctx.get("_queue_poller_task")
    if stop_event is not None:
        stop_event.set()
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception:  # pragma: no cover - defensive
            pass
    poller_redis = ctx.get("_queue_poller_redis")
    if poller_redis is not None:
        try:
            await poller_redis.aclose()
        except Exception:  # pragma: no cover - defensive
            pass

    projection_store = ctx.get("projection_store")
    if projection_store and getattr(projection_store, "coordinator", None):
        await projection_store.coordinator.aclose()
    cache = ctx.get("redis_cache")
    if cache is not None:
        try:
            await cache.close()
        except Exception:  # pragma: no cover - defensive
            pass
    db_pool = ctx.get("db_pool")
    if db_pool:
        await db_pool.close_pool()


@instrument_job("refresh_movie_candidates")
async def refresh_movie_candidates(ctx):
    await ctx["candidate_store"].refresh_movie_candidates()
    # Invalidate the cached qualifying-row counts used by MovieQueryBuilder's
    # random-offset strategy. Stale counts after a refresh can produce empty
    # result sets when the random offset overshoots the new row count.
    cache = ctx.get("redis_cache")
    if cache is not None:
        try:
            from movies.query_builder import bump_count_cache_generation

            await bump_count_cache_generation(cache)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to bump count cache generation after refresh: %s", exc)


@instrument_job("ensure_core_projection")
async def ensure_core_projection(ctx, tconst: str):
    return await ctx["projection_store"].ensure_core_projection(tconst)


@instrument_job("enrich_projection")
async def enrich_projection(ctx, tconst: str, tmdb_id: int | None = None):
    return await ctx["projection_store"].enrich_projection(tconst, known_tmdb_id=tmdb_id)


@instrument_job("requeue_stale_projections")
async def requeue_stale_projections(ctx):
    return await ctx["projection_store"].requeue_stale_projections()


# Bounded concurrency for integrity checks. Conservative cap of 4 keeps
# aggregate pool pressure modest even if checks run on a small connection
# pool; each gathered leg acquires its own pooled connection via execute().
INTEGRITY_CHECK_CONCURRENCY = 4


@instrument_job("validate_referential_integrity")
async def validate_referential_integrity(ctx):
    db_pool = ctx["db_pool"]
    semaphore = asyncio.Semaphore(INTEGRITY_CHECK_CONCURRENCY)

    async def _run_check(query: str):
        async with semaphore:
            return await db_pool.execute(query, fetch="one")

    results = await asyncio.gather(
        *[_run_check(query) for _description, query in INTEGRITY_CHECKS],
        return_exceptions=True,
    )

    issues_found = 0
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Integrity check failed: %s", result)
            continue
        if result and result.get("orphans", 0) > 0:
            issues_found += 1
    return issues_found


@instrument_job("purge_expired_navigation_state")
async def purge_expired_navigation_state(ctx):
    total_deleted = 0
    while True:
        result = await ctx["db_pool"].execute(
            "DELETE FROM user_navigation_state WHERE expires_at < UTC_TIMESTAMP(6) LIMIT 1000",
            fetch="none",
        )
        batch_deleted = result if isinstance(result, int) else 0
        total_deleted += batch_deleted
        if batch_deleted < 1000:
            break
        await asyncio.sleep(PURGE_BATCH_SLEEP_SECONDS)
    if total_deleted:
        logger.info("Purged %d expired navigation states", total_deleted)
    return total_deleted


HOT_PATH_FUNCTIONS = [
    ensure_core_projection,
    enrich_projection,
]

MAINTENANCE_FUNCTIONS = [
    refresh_movie_candidates,
    requeue_stale_projections,
    validate_referential_integrity,
    purge_expired_navigation_state,
]


if RedisSettings is not None:

    # Cron schedules are owned by MaintenanceWorkerSettings so that a
    # two-worker topology (hot-path + maintenance) does NOT run the same
    # cron twice. WorkerSettings still registers every function so ad-hoc
    # and single-worker deploys can enqueue them, but it no longer owns
    # the cron schedule. Single-worker deploys should set
    # NEXTREEL_CRON_ON_HOT_PATH=1 to register the schedule on
    # WorkerSettings instead (or run a separate MaintenanceWorkerSettings
    # process — the recommended topology).
    _HOT_PATH_OWNS_CRON = os.getenv("NEXTREEL_CRON_ON_HOT_PATH", "").lower() in (
        "1",
        "true",
        "yes",
    )

    _MAINTENANCE_CRON_JOBS = [
        # Daily candidate-cache rebuild at 02:17 UTC (off-peak).
        cron(refresh_movie_candidates, hour={2}, minute={17}),
        # Re-queue stale projections hourly at :07.
        cron(requeue_stale_projections, minute={7}),
        # Purge expired nav-state rows four times daily.
        cron(
            purge_expired_navigation_state,
            hour={3, 9, 15, 21},
            minute={23},
        ),
    ]

    class WorkerSettings:  # pragma: no cover - config container
        # Registers every function so a single-worker deploy keeps working.
        # Split the process into two (see MaintenanceWorkerSettings) when you
        # want to isolate the heavy `refresh_movie_candidates` cron from the
        # `enrich_projection` hot path.
        functions = HOT_PATH_FUNCTIONS + MAINTENANCE_FUNCTIONS
        # Cron schedule is empty by default so a co-running
        # MaintenanceWorkerSettings process does not execute every cron
        # twice. Set NEXTREEL_CRON_ON_HOT_PATH=1 for single-worker deploys.
        cron_jobs = _MAINTENANCE_CRON_JOBS if _HOT_PATH_OWNS_CRON else []
        on_startup = startup_hot_path
        on_shutdown = shutdown
        redis_settings = RedisSettings.from_dsn(
            os.getenv("REDIS_URL", "redis://localhost:6379")
        )

    class MaintenanceWorkerSettings:  # pragma: no cover - config container
        """Isolated worker for heavy maintenance cron jobs.

        Start a second arq process pointed at this class to keep multi-minute
        jobs like `refresh_movie_candidates` off the enrichment hot path::

            arq worker.MaintenanceWorkerSettings

        This is the canonical owner of the maintenance cron schedule.
        WorkerSettings.cron_jobs is empty by default so running both
        workers together does not double-execute every cron.
        """

        functions = MAINTENANCE_FUNCTIONS
        cron_jobs = _MAINTENANCE_CRON_JOBS
        on_startup = startup_maintenance
        on_shutdown = shutdown
        redis_settings = RedisSettings.from_dsn(
            os.getenv("REDIS_URL", "redis://localhost:6379")
        )
        queue_name = MAINTENANCE_QUEUE_NAME
