from __future__ import annotations

import hashlib
import json
import random
from typing import Any

from filter_contracts import MovieCriteria
from infra.cache import CacheNamespace
from logging_config import get_logger

logger = get_logger(__name__)

FILTER_RESULT_CACHE_TTL_SECONDS = 30


def filter_cache_key(criteria: MovieCriteria) -> str:
    blob = json.dumps(dict(criteria), sort_keys=True, default=str).encode()
    return "filter_pool:" + hashlib.sha256(blob).hexdigest()[:16]


class CandidateFilterPoolCache:
    def __init__(self, cache=None, *, ttl_seconds: int = FILTER_RESULT_CACHE_TTL_SECONDS):
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    def attach_cache(self, cache) -> None:
        self.cache = cache

    async def sample(
        self,
        *,
        criteria: MovieCriteria,
        excluded_tconsts: set[str],
        limit: int,
    ) -> list[dict[str, Any]] | None:
        if self.cache is None:
            return None
        cache_key = filter_cache_key(criteria)
        try:
            pool = await self.cache.get(CacheNamespace.TEMP, cache_key)
        except Exception:
            return None
        if not pool or not isinstance(pool, list):
            return None
        available = [
            ref
            for ref in pool
            if isinstance(ref, dict) and ref.get("tconst") not in excluded_tconsts
        ]
        if not available:
            return None
        random.shuffle(available)
        return available[:limit]

    async def store(
        self,
        *,
        criteria: MovieCriteria,
        refs: list[dict[str, Any]],
    ) -> None:
        if self.cache is None or not refs:
            return
        cache_key = filter_cache_key(criteria)
        try:
            await self.cache.set(
                CacheNamespace.TEMP,
                cache_key,
                refs,
                ttl=self.ttl_seconds,
            )
        except Exception:
            logger.debug("Filter-pool cache write failed", exc_info=True)
