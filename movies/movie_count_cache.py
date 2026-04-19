from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Callable

from infra.cache import CacheNamespace, LruExpiringMap
from infra.errors import DatabaseError
from logging_config import get_logger

logger = get_logger(__name__)

_COUNT_GENERATION_KEY = "count_generation"


def criteria_cache_key(criteria: dict[str, Any], generation: int = 0) -> str:
    blob = json.dumps(criteria, sort_keys=True, default=str).encode()
    return f"count:{generation}:" + hashlib.sha256(blob).hexdigest()[:16]


async def current_count_generation(cache) -> int:
    if cache is None:
        return 0
    try:
        current = await cache.get(CacheNamespace.TEMP, _COUNT_GENERATION_KEY)
        return int(current) if current is not None else 0
    except Exception:
        return 0


async def bump_count_cache_generation(cache) -> int:
    """Increment the count-cache generation, invalidating all stored counts."""
    if cache is None:
        return 0
    try:
        current = await cache.get(CacheNamespace.TEMP, _COUNT_GENERATION_KEY)
        next_gen = (int(current) if current is not None else 0) + 1
        await cache.set(CacheNamespace.TEMP, _COUNT_GENERATION_KEY, next_gen, ttl=86400 * 7)
        return next_gen
    except Exception:
        logger.warning("Failed to bump count cache generation", exc_info=True)
        return 0


class MovieCountCache:
    # Cold-cache count queries join title.basics and title.ratings which can
    # take multi-second on a freshly-invalidated cache. 1 hour is long enough
    # to absorb any per-hour refresh cadence while still reflecting data
    # changes within acceptable latency for a filter-count hint.
    COUNT_CACHE_TTL = 3600
    COUNT_LOCK_TTL_SECONDS = 30
    COUNT_LOCK_POLL_INTERVAL = 0.1
    COUNT_LOCK_POLL_MAX_WAIT = 5.0

    def __init__(self, cache=None) -> None:
        self.cache = cache
        self._locks: LruExpiringMap = LruExpiringMap(max_keys=512, ttl_seconds=300)
        self._locks_guard = asyncio.Lock()

    async def get_cached_count(self, cache_key: str) -> int | None:
        if not self.cache:
            return None
        try:
            cached = await self.cache.get(CacheNamespace.TEMP, cache_key)
            if cached is not None:
                logger.debug("Count cache hit for %s: %d", cache_key, cached)
                return int(cached)
        except Exception:
            logger.warning("Cache read failed for %s", cache_key, exc_info=True)
        return None

    async def set_cached_count(self, cache_key: str, count: int) -> None:
        if not self.cache:
            return
        try:
            await self.cache.set(CacheNamespace.TEMP, cache_key, count, ttl=self.COUNT_CACHE_TTL)
        except Exception:
            logger.warning("Cache write failed for %s", cache_key, exc_info=True)

    async def acquire_count_lock(self, cache_key: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(cache_key)
            if lock is None:
                self.evict_stale_count_locks(cache_key)
                lock = asyncio.Lock()
                self._locks[cache_key] = lock
            return lock

    def evict_stale_count_locks(self, current_key: str) -> None:
        try:
            current_gen = current_key.split(":", 2)[1]
        except IndexError:
            return
        stale = [key for key in self._locks if key.split(":", 2)[1] != current_gen]
        for key in stale:
            self._locks.pop(key, None)

    async def count_qualifying_rows(
        self,
        *,
        criteria: dict[str, Any],
        parameters: list[Any],
        use_cache: bool,
        use_recent: bool,
        lang: str,
        db_pool,
        query_builder,
        build_query_with_genres: Callable,
    ) -> int:
        generation = await current_count_generation(self.cache)
        cache_key = criteria_cache_key(criteria, generation)

        total_rows = await self.get_cached_count(cache_key)
        if total_rows is not None:
            return total_rows

        lock = await self.acquire_count_lock(cache_key)
        async with lock:
            total_rows = await self.get_cached_count(cache_key)
            if total_rows is not None:
                return total_rows

            return await self.run_count_with_global_lock(
                criteria=criteria,
                parameters=parameters,
                use_cache=use_cache,
                use_recent=use_recent,
                lang=lang,
                cache_key=cache_key,
                db_pool=db_pool,
                query_builder=query_builder,
                build_query_with_genres=build_query_with_genres,
            )

    async def run_count_with_global_lock(
        self,
        *,
        criteria: dict[str, Any],
        parameters: list[Any],
        use_cache: bool,
        use_recent: bool,
        lang: str,
        cache_key: str,
        db_pool,
        query_builder,
        build_query_with_genres: Callable,
    ) -> int:
        lock_key = f"count_lock:{cache_key}"
        acquired_global = False
        if self.cache is not None:
            acquired_global = await self.cache.try_acquire_lock(
                CacheNamespace.TEMP,
                lock_key,
                ttl_seconds=self.COUNT_LOCK_TTL_SECONDS,
            )
        else:
            acquired_global = True

        if not acquired_global:
            waited = 0.0
            while waited < self.COUNT_LOCK_POLL_MAX_WAIT:
                await asyncio.sleep(self.COUNT_LOCK_POLL_INTERVAL)
                waited += self.COUNT_LOCK_POLL_INTERVAL
                total_rows = await self.get_cached_count(cache_key)
                if total_rows is not None:
                    return total_rows

        try:
            count_query = query_builder.build_count_query(
                use_cache=use_cache,
                use_recent=use_recent,
                language=lang,
            )
            count_query, count_params = build_query_with_genres(
                count_query,
                criteria,
                parameters,
                use_cache or use_recent,
            )
            try:
                count_result = await db_pool.execute(count_query, count_params, fetch="one")
            except DatabaseError:
                count_result = None
            total_rows = list(count_result.values())[0] if count_result else 0
            if acquired_global:
                await self.set_cached_count(cache_key, total_rows)
            return total_rows
        finally:
            if acquired_global and self.cache is not None:
                await self.cache.release_lock(CacheNamespace.TEMP, lock_key)
