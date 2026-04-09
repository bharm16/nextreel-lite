"""Simple Redis cache manager — no encryption, no HMAC signing.

This replaces the over-engineered SecureCacheManager for caching publicly
available movie data.  It provides the same API surface so existing callers
(movie_navigator, app.py) work without changes.

See ADR-001-ARCHITECTURE-AUDIT.md, Finding 3.1.
"""

import asyncio
import json
import time
from collections import OrderedDict
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union

import redis.asyncio as aioredis

from infra.time_utils import env_bool
from logging_config import get_logger

# Gate the unversioned legacy-key fallback read so the second Redis RTT
# only happens during a documented migration window. Default off — flip
# CACHE_LEGACY_FALLBACK_ENABLED=true while draining a previous deploy's
# unversioned entries.
_CACHE_LEGACY_FALLBACK_ENABLED = env_bool("CACHE_LEGACY_FALLBACK_ENABLED", False)

logger = get_logger(__name__)


class LruExpiringMap:
    """Bounded LRU map with per-entry TTL expiration.

    - Set is O(1); re-setting an existing key refreshes both LRU position and TTL.
    - Get is O(1); expired entries are evicted on access and return ``default``.
    - Uses ``time.monotonic()`` by default so clock skew cannot resurrect stale entries.
    - Not thread-safe. Each caller owns its own instance.
    """

    def __init__(
        self,
        max_keys: int,
        ttl_seconds: float,
        time_func=None,
    ) -> None:
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._max_keys = max_keys
        self._ttl = ttl_seconds
        self._now = time_func if time_func is not None else time.monotonic
        self._data: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()

    @property
    def max_keys(self) -> int:
        return self._max_keys

    @max_keys.setter
    def max_keys(self, value: int) -> None:
        if value <= 0:
            raise ValueError("max_keys must be positive")
        self._max_keys = value
        while len(self._data) > self._max_keys:
            self._data.popitem(last=False)

    def set(self, key, value) -> None:
        now = self._now()
        self._sweep_expired(now)
        self._data[key] = (now + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max_keys:
            self._data.popitem(last=False)

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        expires_at, value = entry
        if self._now() >= expires_at:
            self._data.pop(key, None)
            return default
        self._data.move_to_end(key)
        return value

    def pop(self, key, default=None):
        entry = self._data.pop(key, None)
        if entry is None:
            return default
        return entry[1]

    def clear(self) -> None:
        self._data.clear()

    def items(self):
        """Yield (key, value) pairs for non-expired entries.

        Expired entries are lazily evicted. Iteration order is LRU (oldest first).
        """
        now = self._now()
        self._sweep_expired(now)
        return [(k, v) for k, (_, v) in self._data.items()]

    def __iter__(self):
        now = self._now()
        self._sweep_expired(now)
        return iter(list(self._data.keys()))

    def __setitem__(self, key, value) -> None:
        self.set(key, value)

    def __getitem__(self, key):
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel:
            raise KeyError(key)
        return value

    def __delitem__(self, key) -> None:
        if key not in self._data:
            raise KeyError(key)
        self._data.pop(key, None)

    def _sweep_expired(self, now: float) -> None:
        expired = [k for k, (exp, _) in self._data.items() if now >= exp]
        for k in expired:
            self._data.pop(k, None)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key) -> bool:
        return self.get(key) is not None

# Bump this whenever a cached payload's schema changes. Old entries are
# read once as a fallback during the transition; writes always use the
# current version prefix.
CACHE_KEY_VERSION = "v1"


class CacheNamespace(Enum):
    """Cache namespaces for different data types."""

    SESSION = "session"
    MOVIE = "movie"
    USER = "user"
    QUEUE = "queue"
    API = "api"
    TEMP = "temp"


class SimpleCacheManager:
    """Lightweight Redis cache — JSON serialization, TTL, namespaced keys.

    Preferred construction via factory classmethods::

        cache = SimpleCacheManager.from_url("redis://localhost:6379")
        cache = SimpleCacheManager.from_pool(shared_pool)
        cache = SimpleCacheManager.from_client(existing_redis)

    The original constructor is preserved for backward compatibility.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        connection_pool: Optional[aioredis.ConnectionPool] = None,
        redis_client: Optional[aioredis.Redis] = None,
        default_ttl: int = 3600,
        verify_connection: bool = True,
        **kwargs,
    ):
        # kwargs accepts (and ignores) secret_key, enable_monitoring, etc.
        # for backward compatibility with SecureCacheManager constructor args.
        self._redis_url = redis_url
        self._connection_pool = connection_pool
        self._default_ttl = default_ttl
        self._owns_connection = False  # True when we created our own connection
        self._redis: Optional[aioredis.Redis] = redis_client
        self._verify_connection = verify_connection
        # In-process single-flight: collapses concurrent loaders for the same
        # key onto a single backing fetch (defends against thundering herd
        # when a hot key expires under load).
        self._inflight: dict[str, asyncio.Future] = {}
        self._inflight_lock = asyncio.Lock()

    @classmethod
    def from_url(cls, redis_url: str, default_ttl: int = 3600) -> "SimpleCacheManager":
        """Create a cache manager that owns its own Redis connection."""
        return cls(redis_url=redis_url, default_ttl=default_ttl)

    @classmethod
    def from_pool(
        cls, connection_pool: aioredis.ConnectionPool, default_ttl: int = 3600
    ) -> "SimpleCacheManager":
        """Create a cache manager sharing an existing connection pool."""
        return cls(connection_pool=connection_pool, default_ttl=default_ttl)

    @classmethod
    def from_client(
        cls,
        redis_client: aioredis.Redis,
        default_ttl: int = 3600,
        verify_connection: bool = True,
    ) -> "SimpleCacheManager":
        """Create a cache manager wrapping an existing Redis client."""
        return cls(
            redis_client=redis_client,
            default_ttl=default_ttl,
            verify_connection=verify_connection,
        )

    async def initialize(self) -> None:
        """Connect to Redis (or verify shared connection)."""
        # If a Redis client was already provided, just verify it works
        if self._redis is not None:
            if not self._verify_connection:
                logger.info("SimpleCacheManager using pre-verified Redis connection")
                return
            try:
                await self._redis.ping()
                logger.info("SimpleCacheManager using shared Redis connection")
            except Exception as e:
                logger.warning("SimpleCacheManager shared Redis unavailable: %s", e)
                self._redis = None
            return

        # If a connection pool was provided, create a client from it
        if self._connection_pool is not None:
            try:
                self._redis = aioredis.Redis(
                    connection_pool=self._connection_pool,
                    decode_responses=True,
                )
                await self._redis.ping()
                logger.info("SimpleCacheManager using shared Redis pool")
            except Exception as e:
                logger.warning("SimpleCacheManager shared pool unavailable: %s", e)
                self._redis = None
            return

        # Fallback: create our own connection from URL
        if self._redis_url:
            try:
                self._redis = aioredis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    retry_on_timeout=True,
                )
                await self._redis.ping()
                self._owns_connection = True
                logger.info("SimpleCacheManager connected to Redis (own connection)")
            except Exception as e:
                logger.warning("SimpleCacheManager Redis unavailable: %s", e)
                self._redis = None

    async def close(self) -> None:
        """Close the Redis connection only if we created it ourselves."""
        if self._redis and self._owns_connection:
            try:
                await self._redis.aclose()
            except Exception as e:
                logger.debug("Error closing SimpleCacheManager: %s", e)
        self._redis = None

    def _make_key(self, namespace: CacheNamespace, key: str) -> str:
        return f"cache:{CACHE_KEY_VERSION}:{namespace.value}:{key}"

    def _make_legacy_key(self, namespace: CacheNamespace, key: str) -> str:
        """Pre-versioning key format. Read-only compat during migration."""
        return f"cache:{namespace.value}:{key}"

    async def get(self, namespace: CacheNamespace, key: str) -> Optional[Any]:
        """Retrieve a cached value, or None on miss/error.

        Reads the current versioned key first; on miss, attempts a single
        read of the legacy (unversioned) key so entries written by a prior
        deploy remain visible during a transition.
        """
        if not self._redis:
            return None
        try:
            raw = await self._redis.get(self._make_key(namespace, key))
            if raw is None:
                if not _CACHE_LEGACY_FALLBACK_ENABLED:
                    return None
                # Backward-compat: try the legacy unversioned key once.
                raw = await self._redis.get(self._make_legacy_key(namespace, key))
                if raw is None:
                    return None
            return json.loads(raw)
        except Exception as e:
            logger.debug("Cache get failed for %s:%s — %s", namespace.value, key, e)
            return None

    async def set(
        self,
        namespace: CacheNamespace,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> None:
        """Store a value with optional TTL (seconds)."""
        if not self._redis:
            return
        try:
            ttl = ttl or self._default_ttl
            await self._redis.setex(
                self._make_key(namespace, key),
                ttl,
                json.dumps(value, default=str),
            )
        except Exception as e:
            logger.debug("Cache set failed for %s:%s — %s", namespace.value, key, e)

    async def get_or_load(
        self,
        namespace: CacheNamespace,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        ttl: Optional[int] = None,
    ) -> Any:
        """Single-flight cache lookup.

        On a cache hit, returns immediately. On a miss, only the *first*
        concurrent caller invokes ``loader()``; the rest await its result.
        Prevents thundering-herd backend calls when a hot key expires.
        """
        cached = await self.get(namespace, key)
        if cached is not None:
            return cached

        flight_key = self._make_key(namespace, key)
        async with self._inflight_lock:
            future = self._inflight.get(flight_key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[flight_key] = future
                owns = True
            else:
                owns = False

        if not owns:
            return await future

        try:
            cached = await self.get(namespace, key)
            if cached is not None:
                future.set_result(cached)
                return cached
            value = await loader()
            if value is not None:
                await self.set(namespace, key, value, ttl=ttl)
            future.set_result(value)
            return value
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            async with self._inflight_lock:
                self._inflight.pop(flight_key, None)

    async def safe_get_or_set(
        self,
        namespace: CacheNamespace,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        ttl: Optional[int] = None,
    ) -> Any:
        """Get from cache, fall back to loader on miss, write back on hit.

        Swallows and logs cache-layer exceptions so a Redis outage degrades
        to direct loader calls instead of crashing the caller. Unlike
        ``get_or_load``, this helper does not perform single-flight
        coalescing — it's intended as a drop-in replacement for hand-rolled
        "try cache, swallow errors, call loader, write back" blocks.
        """
        try:
            cached = await self.get(namespace, key)
            if cached is not None:
                return cached
        except Exception as exc:
            logger.warning(
                "cache get failed for %s:%s — %s", namespace.value, key, exc
            )
        value = await loader()
        if value is not None:
            try:
                await self.set(namespace, key, value, ttl=ttl)
            except Exception as exc:
                logger.warning(
                    "cache set failed for %s:%s — %s", namespace.value, key, exc
                )
        return value

    async def delete(self, namespace: CacheNamespace, key: str) -> None:
        """Delete a cached key."""
        if not self._redis:
            return
        try:
            await self._redis.delete(self._make_key(namespace, key))
        except Exception as e:
            logger.debug("Cache delete failed for %s:%s — %s", namespace.value, key, e)

    async def try_acquire_lock(
        self,
        namespace: CacheNamespace,
        key: str,
        ttl_seconds: int,
    ) -> bool:
        """Attempt to claim a distributed lock via SET NX PX.

        Returns True if this caller acquired the lock, False if another
        worker already holds it. TTL acts as a safety release in case the
        holder crashes. Callers should ``release_lock`` when done if they
        want to shorten the window; otherwise the TTL expiry is enough.

        On Redis unavailability, returns True so callers fall back to
        whatever in-process guard they already have — we prefer
        fail-open (let work proceed) over fail-closed (block everything).
        """
        if not self._redis:
            return True
        try:
            got = await self._redis.set(
                self._make_key(namespace, key),
                "1",
                nx=True,
                ex=ttl_seconds,
            )
            return bool(got)
        except Exception as e:
            logger.debug(
                "Lock acquire failed for %s:%s — %s (fail-open)",
                namespace.value,
                key,
                e,
            )
            return True

    async def release_lock(self, namespace: CacheNamespace, key: str) -> None:
        """Release a distributed lock acquired via ``try_acquire_lock``.

        Safe to call even if the lock has already expired.
        """
        if not self._redis:
            return
        try:
            await self._redis.delete(self._make_key(namespace, key))
        except Exception as e:
            logger.debug(
                "Lock release failed for %s:%s — %s",
                namespace.value,
                key,
                e,
            )
