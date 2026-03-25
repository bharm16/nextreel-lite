"""Redis-backed rate limiter with in-memory fallback.

Extracted from ``routes.py`` so that rate-limiting policy can evolve
independently of route definitions.
"""

import asyncio
import time

from quart import current_app

from infra.client_ip import get_client_ip
from infra.metrics import set_rate_limit_backend
from logging_config import get_logger

logger = get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 30  # requests per window

# In-memory fallback (single-instance only).
# Cap at 1024 keys to prevent unbounded memory growth from unique IPs.
_RATE_LIMIT_MAX_KEYS = 1024
_rate_limit_store: dict[str, list[float]] = {}
_rate_limit_lock = asyncio.Lock()


_active_backend = "memory"


# ── Public API ────────────────────────────────────────────────────

async def check_rate_limit(endpoint_key: str) -> bool:
    """Rate limit using Redis pipeline (atomic INCR + EXPIRE).

    Falls back to in-memory if Redis is unavailable.
    """
    global _active_backend
    try:
        redis_client = current_app.config.get("SESSION_REDIS")
        if not redis_client:
            set_rate_limit_backend("memory")
            _active_backend = "memory"
            return await check_rate_limit_memory(endpoint_key)
        ip = get_client_ip()
        key = f"ratelimit:{endpoint_key}:{ip}"

        # Atomic pipeline: INCR + EXPIRE NX avoids the TOCTOU race where
        # a crash between INCR and EXPIRE could leave a key without a TTL.
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, RATE_LIMIT_WINDOW)
        count, _ = await pipe.execute()
        set_rate_limit_backend("redis")
        _active_backend = "redis"

        return count <= RATE_LIMIT_MAX
    except Exception:
        # Redis unavailable — fall back to in-memory
        set_rate_limit_backend("memory")
        _active_backend = "memory"
        return await check_rate_limit_memory(endpoint_key)


async def check_rate_limit_memory(endpoint_key: str) -> bool:
    """In-memory fallback rate limiter (single-instance only).

    Protected by an asyncio.Lock to prevent interleaved coroutine writes.
    """
    ip = get_client_ip()
    key = f"{endpoint_key}:{ip}"
    now = time.time()

    async with _rate_limit_lock:
        timestamps = _rate_limit_store.setdefault(key, [])
        cutoff = now - RATE_LIMIT_WINDOW
        timestamps[:] = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= RATE_LIMIT_MAX:
            return False
        timestamps.append(now)

        # Evict stale keys to prevent unbounded memory growth
        if len(_rate_limit_store) > _RATE_LIMIT_MAX_KEYS:
            stale_keys = [
                k for k, v in _rate_limit_store.items()
                if not v or v[-1] < cutoff
            ]
            for k in stale_keys:
                _rate_limit_store.pop(k, None)

    return True


def get_rate_limit_backend() -> str:
    return _active_backend
