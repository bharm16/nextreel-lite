"""Ops and health route handlers."""

from __future__ import annotations

import asyncio
import time

from quart import current_app

from infra.ops_auth import check_ops_auth
from infra.rate_limit import check_rate_limit, get_rate_limit_backend
from nextreel.web.routes.shared import _services, bp, logger

_READY_CACHE_TTL_SECONDS = 5.0
_ready_cache_entry: tuple[float, tuple[dict, int]] | None = None
_ready_cache_lock = asyncio.Lock()


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


async def _compute_readiness(movie_manager) -> tuple[dict, int]:
    pool_metrics = await movie_manager.db_pool.get_metrics()
    if pool_metrics["circuit_breaker_state"] == "open":
        return {"status": "not_ready", "reason": "database_circuit_breaker_open"}, 503

    navigation_ready = await current_app.navigation_state_store.ready_check()
    candidates_fresh = await movie_manager.candidate_store.has_fresh_data()
    projection_ready = await movie_manager.projection_store.ready_check()
    status_code = 200 if navigation_ready and candidates_fresh and projection_ready else 503

    body = {
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
    }
    return body, status_code


@bp.route("/ready")
async def readiness_check():
    movie_manager = _services().movie_manager
    if not check_ops_auth():
        return {"error": "unauthorized"}, 401
    if not await check_rate_limit("ready"):
        return {"error": "rate limited"}, 429

    global _ready_cache_entry
    cached = _ready_cache_entry
    if cached and time.time() - cached[0] < _READY_CACHE_TTL_SECONDS:
        return cached[1]

    async with _ready_cache_lock:
        cached = _ready_cache_entry
        if cached and time.time() - cached[0] < _READY_CACHE_TTL_SECONDS:
            return cached[1]
        try:
            result = await _compute_readiness(movie_manager)
        except Exception as exc:
            logger.error("Readiness check failed: %s", exc)
            result = ({"status": "not_ready", "reason": "internal_error"}, 503)
        _ready_cache_entry = (time.time(), result)
        return result

__all__ = ["health_check", "metrics", "readiness_check"]
