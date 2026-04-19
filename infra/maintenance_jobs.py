"""Concrete worker job bodies extracted from worker bootstrap."""

from __future__ import annotations

import asyncio

from logging_config import get_logger
from movies.query_builder import bump_count_cache_generation

logger = get_logger(__name__)


_COUNT_CACHE_BUMP_DELTA_THRESHOLD = 1


async def refresh_movie_candidates_job(ctx):
    result = await ctx["candidate_store"].refresh_movie_candidates()
    cache = ctx.get("redis_cache")
    if cache is None:
        return result

    # Only bump the cached-count generation when the refresh actually changed
    # the underlying dataset. The cron fires hourly even when upstream IMDb
    # deltas are zero; bumping unconditionally would force every filter-count
    # query to re-execute the expensive title.basics JOIN even though the
    # answer is unchanged.
    prev = int(result.get("prev_count", 0)) if isinstance(result, dict) else 0
    new = int(result.get("new_count", 0)) if isinstance(result, dict) else 0
    if abs(new - prev) < _COUNT_CACHE_BUMP_DELTA_THRESHOLD:
        logger.debug(
            "Skipping count cache bump; delta=%d below threshold", new - prev
        )
        return result
    try:
        await bump_count_cache_generation(cache)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to bump count cache generation after refresh: %s", exc)
    return result


async def ensure_core_projection_job(ctx, tconst: str):
    return await ctx["projection_store"].ensure_core_projection(tconst)


async def enrich_projection_job(ctx, tconst: str, tmdb_id: int | None = None):
    return await ctx["projection_store"].enrich_projection(tconst, known_tmdb_id=tmdb_id)


async def requeue_stale_projections_job(ctx):
    return await ctx["projection_store"].requeue_stale_projections()


async def validate_referential_integrity_job(ctx, *, integrity_checks, concurrency: int):
    db_pool = ctx["db_pool"]
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_check(query: str):
        async with semaphore:
            return await db_pool.execute(query, fetch="one")

    results = await asyncio.gather(
        *[_run_check(query) for _description, query in integrity_checks],
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


async def purge_expired_navigation_state_job(
    ctx,
    *,
    sql: str,
    batch_size: int,
    batch_sleep_seconds: float,
):
    total_deleted = 0
    while True:
        result = await ctx["db_pool"].execute(
            sql,
            [batch_size],
            fetch="none",
        )
        batch_deleted = result if isinstance(result, int) else 0
        total_deleted += batch_deleted
        if batch_deleted < batch_size:
            break
        await asyncio.sleep(batch_sleep_seconds)
    if total_deleted:
        logger.info("Purged %d expired navigation states", total_deleted)
    return total_deleted
