"""Redis-backed rate limiter with in-memory fallback.

Extracted from ``routes.py`` so that rate-limiting policy can evolve
independently of route definitions.
"""

import asyncio
import time

from quart import current_app

from infra.cache import LruExpiringMap
from infra.client_ip import get_client_ip
from infra.metrics import set_rate_limit_backend
from logging_config import get_logger

logger = get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 30  # requests per window

# Per-endpoint overrides for endpoints whose natural request shape differs
# from the default. Browser-SDK proxy traffic batches pageview, autocapture,
# and session-recording snapshots — a single normal session legitimately
# exceeds 30/min when session replay is on. The override is high enough
# for legitimate traffic but still bounded so a single IP can't be used
# to relay arbitrary bandwidth through us.
_ENDPOINT_LIMIT_OVERRIDES: dict[str, int] = {
    "posthog_proxy": 300,
}


def _max_for(endpoint_key: str) -> int:
    return _ENDPOINT_LIMIT_OVERRIDES.get(endpoint_key, RATE_LIMIT_MAX)

# In-memory fallback (single-instance only).
# Cap at 1024 keys to prevent unbounded memory growth from unique IPs.
# LruExpiringMap handles LRU eviction + TTL-based expiration in one data
# structure (replaces a hand-rolled OrderedDict + manual stale scan).
_RATE_LIMIT_MAX_KEYS = 1024
_rate_limit_store: LruExpiringMap = LruExpiringMap(
    max_keys=_RATE_LIMIT_MAX_KEYS,
    ttl_seconds=RATE_LIMIT_WINDOW,
    time_func=time.time,
)
_rate_limit_lock = asyncio.Lock()


_active_backend = "memory"
_memory_fallback_warned = False


def _build_ratelimit_key(endpoint_key: str, ip: str) -> str:
    """Unified rate-limit key format used by both Redis and memory backends."""
    return f"ratelimit:{endpoint_key}:{ip}"


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
        key = _build_ratelimit_key(endpoint_key, ip)

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

        return count <= _max_for(endpoint_key)
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
    key = _build_ratelimit_key(endpoint_key, ip)
    now = time.time()

    async with _rate_limit_lock:
        timestamps = _rate_limit_store.get(key)
        if timestamps is None:
            timestamps = []
        cutoff = now - RATE_LIMIT_WINDOW
        timestamps = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= _max_for(endpoint_key):
            # Refresh LRU/TTL so repeated offenders don't age out mid-window.
            _rate_limit_store.set(key, timestamps)
            return False
        timestamps.append(now)
        _rate_limit_store.set(key, timestamps)

    return True


def get_rate_limit_backend() -> str:
    return _active_backend
