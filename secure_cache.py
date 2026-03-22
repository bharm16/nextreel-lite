"""Compatibility wrapper around the lightweight Redis cache.

The old SecureCacheManager implementation added HMAC signing and encryption
for public movie data. Runtime code now uses the simpler JSON cache directly.
This module keeps the import path stable for older modules and scripts.
"""

from functools import wraps
from typing import Any, Optional

from quart import current_app

from logging_config import get_logger
from simple_cache import CacheNamespace, SimpleCacheManager

logger = get_logger(__name__)


class SecureCacheManager(SimpleCacheManager):
    """Backward-compatible alias for the simplified cache manager."""

    def __init__(
        self,
        redis_url: str,
        secret_key: Optional[str] = None,
        policies: Optional[dict] = None,
        enable_monitoring: bool = True,
        default_ttl: int = 3600,
    ) -> None:
        super().__init__(redis_url=redis_url, default_ttl=default_ttl)
        self.secret_key = secret_key
        self.policies = policies or {}
        self.enable_monitoring = enable_monitoring

    async def delete_pattern(self, namespace: CacheNamespace, pattern: str) -> int:
        """Delete matching keys when Redis is available."""
        if not self._redis:
            return 0

        deleted = 0
        async for key in self._redis.scan_iter(match=self._make_key(namespace, pattern)):
            await self._redis.delete(key)
            deleted += 1
        return deleted

    async def invalidate_namespace(self, namespace: CacheNamespace) -> int:
        """Delete all cached entries in a namespace."""
        return await self.delete_pattern(namespace, "*")

    async def get_metrics(self) -> dict[str, Any]:
        """Return lightweight cache metrics."""
        return {"backend": "redis" if self._redis else "disabled"}


def cache_response(ttl: int = 60, namespace: CacheNamespace = CacheNamespace.API):
    """Cache decorator using the app's configured cache instance."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            secure_cache = getattr(current_app, "secure_cache", None)
            cache_key = f"{func.__module__}.{func.__name__}:{args!r}:{kwargs!r}"

            if secure_cache:
                try:
                    cached = await secure_cache.get(namespace, cache_key)
                    if cached is not None:
                        return cached
                except Exception as exc:
                    logger.debug("Cache lookup failed for %s: %s", cache_key, exc)

            result = await func(*args, **kwargs)

            if secure_cache and result is not None:
                try:
                    await secure_cache.set(namespace, cache_key, result, ttl=ttl)
                except Exception as exc:
                    logger.debug("Cache write failed for %s: %s", cache_key, exc)

            return result

        return wrapper

    return decorator
