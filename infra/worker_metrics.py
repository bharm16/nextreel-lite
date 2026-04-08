"""Worker-process observability: Prometheus HTTP endpoint, job instrumentation,
and an ARQ queue-depth poller.

This module is designed to be imported safely by the worker entrypoint. It
never starts any servers or tasks at import time; callers must explicitly
invoke ``start_worker_metrics_server`` and ``start_queue_poller``.
"""

from __future__ import annotations

import asyncio
import functools
import os
import time
from typing import Awaitable, Callable, Optional

from logging_config import get_logger

logger = get_logger(__name__)

# Defaults documented in CLAUDE.md / config surface.
DEFAULT_METRICS_HOST = "127.0.0.1"
DEFAULT_METRICS_PORT = 8001
DEFAULT_POLL_INTERVAL = 15.0
DEFAULT_QUEUE_KEY = "arq:queue"


def _resolve_metrics_host() -> str:
    return os.getenv("WORKER_METRICS_HOST", DEFAULT_METRICS_HOST)


def _resolve_metrics_port(default: int = DEFAULT_METRICS_PORT) -> int:
    raw = os.getenv("WORKER_METRICS_PORT")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid WORKER_METRICS_PORT=%r, falling back to %d", raw, default
        )
        return default


def _resolve_poll_interval() -> float:
    raw = os.getenv("WORKER_METRICS_POLL_INTERVAL")
    if not raw:
        return DEFAULT_POLL_INTERVAL
    try:
        value = float(raw)
        return max(1.0, value)
    except ValueError:
        return DEFAULT_POLL_INTERVAL


def _resolve_queue_key(worker_settings: Optional[type] = None) -> str:
    """Prefer the explicit env var, then the WorkerSettings.queue_name
    attribute, then the arq default."""
    env_value = os.getenv("ARQ_QUEUE_KEY")
    if env_value:
        return env_value
    if worker_settings is not None:
        queue_name = getattr(worker_settings, "queue_name", None)
        if queue_name:
            return queue_name
    return DEFAULT_QUEUE_KEY


# ---------------------------------------------------------------------------
# HTTP metrics endpoint
# ---------------------------------------------------------------------------


def start_worker_metrics_server(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> bool:
    """Start a standalone Prometheus HTTP endpoint for the worker process.

    Returns True on success, False if the port bind fails or prometheus_client
    is unavailable. Never raises — the worker must start even if metrics do not.
    """
    try:
        from prometheus_client import start_http_server
    except Exception as exc:  # pragma: no cover - prometheus_client is a direct dep
        logger.warning("prometheus_client unavailable, worker metrics disabled: %s", exc)
        return False

    bind_host = host if host is not None else _resolve_metrics_host()
    bind_port = port if port is not None else _resolve_metrics_port()
    try:
        start_http_server(bind_port, addr=bind_host)
        logger.info(
            "Worker Prometheus metrics server listening on %s:%d", bind_host, bind_port
        )
        return True
    except OSError as exc:
        # Common case: two workers on the same host, second bind fails.
        logger.warning(
            "Failed to bind worker metrics server on %s:%d (%s). "
            "Set WORKER_METRICS_PORT to a unique value per worker process.",
            bind_host,
            bind_port,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Job instrumentation decorator
# ---------------------------------------------------------------------------


def instrument_job(job_name: Optional[str] = None):
    """Decorator that emits ``nextreel_worker_jobs_total`` and
    ``nextreel_worker_job_duration_seconds`` for an ARQ job coroutine.

    Example::

        @instrument_job()
        async def refresh_movie_candidates(ctx):
            ...
    """

    def decorator(func: Callable[..., Awaitable]):
        name = job_name or getattr(func, "__name__", "unknown")

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            from infra.metrics import (
                worker_jobs_total,
                worker_job_duration_seconds,
            )

            start = time.time()
            try:
                worker_jobs_total.labels(job_name=name, status="started").inc()
            except Exception:  # pragma: no cover - metrics never break jobs
                pass
            try:
                result = await func(*args, **kwargs)
            except Exception:
                duration = time.time() - start
                try:
                    worker_jobs_total.labels(job_name=name, status="failed").inc()
                    worker_job_duration_seconds.labels(job_name=name).observe(duration)
                except Exception:
                    pass
                logger.exception(
                    "Worker job %s failed after %.2fs", name, duration
                )
                raise
            duration = time.time() - start
            try:
                worker_jobs_total.labels(job_name=name, status="completed").inc()
                worker_job_duration_seconds.labels(job_name=name).observe(duration)
            except Exception:
                pass
            logger.info("Worker job %s completed in %.2fs", name, duration)
            return result

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Queue depth poller
# ---------------------------------------------------------------------------


async def _poll_queue_once(redis_client, queue_key: str) -> None:
    """Read queue depth and oldest-job age from Redis, export as gauges."""
    from infra.metrics import (
        worker_queue_depth,
        worker_queue_oldest_job_age_seconds,
    )

    try:
        depth = await redis_client.zcard(queue_key)
    except Exception as exc:  # pragma: no cover - best-effort
        logger.debug("queue depth poll failed: %s", exc)
        return
    try:
        worker_queue_depth.set(float(depth or 0))
    except Exception:
        pass

    try:
        # arq stores jobs in a sorted set scored by enqueue time (ms).
        oldest = await redis_client.zrange(queue_key, 0, 0, withscores=True)
    except Exception as exc:
        logger.debug("queue oldest-age poll failed: %s", exc)
        return

    if not oldest:
        try:
            worker_queue_oldest_job_age_seconds.set(0.0)
        except Exception:
            pass
        return

    try:
        # ``oldest`` is a list of (member, score) tuples.
        _member, score = oldest[0]
        enqueue_time = float(score) / 1000.0  # ms → s
        age = max(0.0, time.time() - enqueue_time)
        worker_queue_oldest_job_age_seconds.set(age)
    except Exception:
        pass


async def run_queue_poller(
    redis_client,
    queue_key: str,
    *,
    interval: Optional[float] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Long-running poll loop. Call via ``asyncio.create_task``."""
    poll_interval = interval if interval is not None else _resolve_poll_interval()
    stop_event = stop_event or asyncio.Event()
    logger.info(
        "Worker queue poller started (key=%s interval=%.1fs)", queue_key, poll_interval
    )
    while not stop_event.is_set():
        try:
            await _poll_queue_once(redis_client, queue_key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("queue poller iteration failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            continue


def resolve_queue_key(worker_settings: Optional[type] = None) -> str:
    """Public accessor used by worker.py so callers don't duplicate the
    env/attribute/default precedence logic."""
    return _resolve_queue_key(worker_settings)
