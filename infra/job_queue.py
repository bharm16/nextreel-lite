from __future__ import annotations

import asyncio

from infra.metrics import enrichment_enqueue_fallback_total, enrichment_enqueued_total
from infra.metrics_groups import safe_emit
from logging_config import get_logger

try:
    from arq import create_pool as create_arq_pool
    from arq.connections import RedisSettings
except ImportError:  # pragma: no cover - optional dependency at import time
    create_arq_pool = None
    RedisSettings = None

logger = get_logger(__name__)


class RuntimeJobQueue:
    def __init__(
        self,
        app,
        *,
        create_pool_fn=create_arq_pool,
        redis_settings_cls=RedisSettings,
    ) -> None:
        self.app = app
        self.create_pool_fn = create_pool_fn
        self.redis_settings_cls = redis_settings_cls
        self._lock = asyncio.Lock()

    async def ensure_pool(self):
        if self.app.arq_redis:
            return self.app.arq_redis
        if (
            not self.app.redis_available
            or not self.app.redis_url
            or not self.create_pool_fn
            or not self.redis_settings_cls
        ):
            return None

        async with self._lock:
            if self.app.arq_redis:
                return self.app.arq_redis
            try:
                self.app.arq_redis = await self.create_pool_fn(
                    self.redis_settings_cls.from_dsn(self.app.redis_url)
                )
                self.app.worker_available = True
                logger.info("ARQ pool initialized lazily")
            except Exception as exc:
                self.app.worker_available = False
                logger.warning("ARQ pool initialization failed: %s", exc)
                self.app.arq_redis = None
            return self.app.arq_redis

    async def enqueue_runtime_job(self, function_name: str, *args, **kwargs):
        pool = await self.ensure_pool()
        if not pool:
            safe_emit(lambda: enrichment_enqueue_fallback_total.labels(reason="no_pool").inc())
            return None
        try:
            result = await pool.enqueue_job(function_name, *args, **kwargs)
        except Exception:
            safe_emit(lambda: enrichment_enqueue_fallback_total.labels(reason="enqueue_error").inc())
            raise
        if result is not None:
            safe_emit(enrichment_enqueued_total.inc)
        return result


def install_runtime_job_queue(app, movie_manager) -> RuntimeJobQueue:
    queue = RuntimeJobQueue(app)
    app.runtime_job_queue = queue
    app.enqueue_runtime_job = queue.enqueue_runtime_job
    movie_manager.projection_store.enqueue_fn = queue.enqueue_runtime_job
    return queue
