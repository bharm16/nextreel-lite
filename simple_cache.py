"""Simple Redis cache manager — no encryption, no HMAC signing.

This replaces the over-engineered SecureCacheManager for caching publicly
available movie data.  It provides the same API surface so existing callers
(movie_navigator, app.py) work without changes.

See ADR-001-ARCHITECTURE-AUDIT.md, Finding 3.1.
"""

import json
from enum import Enum
from typing import Any, Optional

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
    """Lightweight Redis cache — JSON serialization, TTL, namespaced keys."""

    def __init__(self, redis_url: str, default_ttl: int = 3600, **kwargs):
        # kwargs accepts (and ignores) secret_key, enable_monitoring, etc.
        # for backward compatibility with SecureCacheManager constructor args.
        self._redis_url = redis_url
        self._default_ttl = default_ttl
        self._redis: Optional[aioredis.Redis] = None

    async def initialize(self) -> None:
        """Connect to Redis."""
        try:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            # Verify connectivity
            await self._redis.ping()
            logger.info("SimpleCacheManager connected to Redis")
        except Exception as e:
            logger.warning("SimpleCacheManager Redis unavailable: %s", e)
            self._redis = None

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis:
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
