"""Simple Redis cache manager — no encryption, no HMAC signing.

This replaces the over-engineered SecureCacheManager for caching publicly
available movie data.  It provides the same API surface so existing callers
(movie_navigator, app.py) work without changes.

See ADR-001-ARCHITECTURE-AUDIT.md, Finding 3.1.
"""

import json
from enum import Enum
from typing import Any, Optional, Union

import redis.asyncio as aioredis

from logging_config import get_logger

logger = get_logger(__name__)


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
        return f"cache:{namespace.value}:{key}"

    async def get(self, namespace: CacheNamespace, key: str) -> Optional[Any]:
        """Retrieve a cached value, or None on miss/error."""
        if not self._redis:
            return None
        try:
            raw = await self._redis.get(self._make_key(namespace, key))
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

    async def delete(self, namespace: CacheNamespace, key: str) -> None:
        """Delete a cached key."""
        if not self._redis:
            return
        try:
            await self._redis.delete(self._make_key(namespace, key))
        except Exception as e:
            logger.debug("Cache delete failed for %s:%s — %s", namespace.value, key, e)
