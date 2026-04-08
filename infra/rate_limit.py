"""Redis-backed rate limiter with in-memory fallback.

Extracted from ``routes.py`` so that rate-limiting policy can evolve
independently of route definitions.
"""

import asyncio
import time
from collections import OrderedDict

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
# Backed by an OrderedDict so we can evict the LRU entry when the cap is hit
# even when every entry has fresh activity (the previous "stale only" eviction
# leaked under sustained traffic from >1024 distinct IPs).
_RATE_LIMIT_MAX_KEYS = 1024
_rate_limit_store: "OrderedDict[str, list[float]]" = OrderedDict()
_rate_limit_lock = asyncio.Lock()


_active_backend = "memory"
_memory_fallback_warned = False


def _warn_memory_fallback_once(reason: str) -> None:
    """Emit a single loud warning the first time we fall back to in-memory.

    The in-memory limiter is per-process: if the app is horizontally scaled
    to N workers without Redis, the effective limit is N × configured limit.
    Ops must know about this explicitly — a per-request debug log is not
    enough.
    """
    global _memory_fallback_warned
    if _memory_fallback_warned:
        return
    _memory_fallback_warned = True
    logger.error(
        "RATE LIMITER DEGRADED: falling back to in-memory store (reason: %s). "
        "This limiter is PER-PROCESS — multi-worker deployments will have "
        "effective limits of N × configured. Restore Redis to re-enable "
        "shared limits.",
        reason,
    )


# ── Public API ────────────────────────────────────────────────────


async def check_rate_limit(endpoint_key: str) -> bool:
    """Rate limit using Redis pipeline (atomic INCR + EXPIRE).

    Falls back to in-memory if Redis is unavailable.
    """
    global _active_backend
    try:
        redis_client = current_app.config.get("SESSION_REDIS")
        if not redis_client:
            if _active_backend != "memory":
                set_rate_limit_backend("memory")
                _active_backend = "memory"
            _warn_memory_fallback_once("SESSION_REDIS not configured")
            return await check_rate_limit_memory(endpoint_key)
        ip = get_client_ip()
        key = f"ratelimit:{endpoint_key}:{ip}"

        # Atomic pipeline: INCR + EXPIRE NX avoids the TOCTOU race where
        # a crash between INCR and EXPIRE could leave a key without a TTL.
        # NX ensures the TTL is only set when the key is first created,
        # preventing window resets on subsequent requests.
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, RATE_LIMIT_WINDOW, nx=True)
        count, _ = await pipe.execute()
        if _active_backend != "redis":
            set_rate_limit_backend("redis")
            _active_backend = "redis"

        return count <= RATE_LIMIT_MAX
    except Exception as exc:
        logger.warning("Redis rate-limit unavailable, falling back to memory: %s", exc)
        if _active_backend != "memory":
            set_rate_limit_backend("memory")
            _active_backend = "memory"
        _warn_memory_fallback_once(f"redis error: {exc}")
        return await check_rate_limit_memory(endpoint_key)


async def check_rate_limit_memory(endpoint_key: str) -> bool:
    """In-memory fallback rate limiter (single-instance only).

    Protected by an asyncio.Lock to prevent interleaved coroutine writes.
    """
    ip = get_client_ip()
    key = f"{endpoint_key}:{ip}"
    now = time.time()

    async with _rate_limit_lock:
        if key in _rate_limit_store:
            timestamps = _rate_limit_store[key]
            _rate_limit_store.move_to_end(key)
        else:
            timestamps = []
            _rate_limit_store[key] = timestamps
        cutoff = now - RATE_LIMIT_WINDOW
        timestamps[:] = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= RATE_LIMIT_MAX:
            return False
        timestamps.append(now)

        # Evict expired entries first, then fall back to LRU eviction so we
        # never grow unbounded even when all entries are "fresh".
        if len(_rate_limit_store) > _RATE_LIMIT_MAX_KEYS:
            stale_keys = [k for k, v in _rate_limit_store.items() if not v or v[-1] < cutoff]
            for k in stale_keys:
                _rate_limit_store.pop(k, None)
            while len(_rate_limit_store) > _RATE_LIMIT_MAX_KEYS:
                _rate_limit_store.popitem(last=False)

    return True


def get_rate_limit_backend() -> str:
    return _active_backend
