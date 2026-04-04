"""ARQ worker entrypoint for runtime maintenance jobs."""

from __future__ import annotations

import os

from infra.integrity_checks import INTEGRITY_CHECKS
from infra.pool import DatabaseConnectionPool
from logging_config import get_logger
from movies.candidate_store import CandidateStore
from movies.projection_store import ProjectionStore
from settings import Config

logger = get_logger(__name__)

try:
    from arq.connections import RedisSettings
except ImportError:  # pragma: no cover - exercised only when arq is missing
    RedisSettings = None


async def startup(ctx):
    db_pool = DatabaseConnectionPool(Config.get_db_config())
    await db_pool.init_pool()
    ctx["db_pool"] = db_pool
    ctx["candidate_store"] = CandidateStore(db_pool)
    ctx["projection_store"] = ProjectionStore(db_pool)
    ctx["projection_coordinator"] = ctx["projection_store"].coordinator
    logger.info("Worker context initialized")


async def shutdown(ctx):
    projection_store = ctx.get("projection_store")
    if projection_store and getattr(projection_store, "coordinator", None):
        await projection_store.coordinator.aclose()
    db_pool = ctx.get("db_pool")
    if db_pool:
        await db_pool.close_pool()


async def refresh_movie_candidates(ctx):
    await ctx["candidate_store"].refresh_movie_candidates()


async def ensure_core_projection(ctx, tconst: str):
    return await ctx["projection_store"].ensure_core_projection(tconst)


async def enrich_projection(ctx, tconst: str, tmdb_id: int | None = None):
    return await ctx["projection_store"].enrich_projection(tconst, known_tmdb_id=tmdb_id)


async def requeue_stale_projections(ctx):
    return await ctx["projection_store"].requeue_stale_projections()


async def validate_referential_integrity(ctx):
    issues_found = 0
    for _description, query in INTEGRITY_CHECKS:
        result = await ctx["db_pool"].execute(query, fetch="one")
        if result and result.get("orphans", 0) > 0:
            issues_found += 1
    return issues_found


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
    if total_deleted:
        logger.info("Purged %d expired navigation states", total_deleted)
    return total_deleted


if RedisSettings is not None:
    class WorkerSettings:  # pragma: no cover - config container
        functions = [
            refresh_movie_candidates,
            ensure_core_projection,
            enrich_projection,
            requeue_stale_projections,
            validate_referential_integrity,
            purge_expired_navigation_state,
        ]
        on_startup = startup
        on_shutdown = shutdown
        redis_settings = RedisSettings.from_dsn(
            os.getenv("REDIS_URL", "redis://localhost:6379")
        )
