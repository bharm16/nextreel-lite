"""Tests for SimpleCacheManager helpers."""

import pytest
from unittest.mock import AsyncMock

from infra.cache import CacheNamespace, LruExpiringMap, SimpleCacheManager


class _FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def advance(self, seconds: float):
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def test_lru_expiring_map_set_and_get():
    clock = _FakeClock()
    m = LruExpiringMap(max_keys=3, ttl_seconds=60.0, time_func=clock)
    m.set("a", 1)
    assert m.get("a") == 1
    assert len(m) == 1


def test_lru_expiring_map_get_returns_default_on_miss():
    clock = _FakeClock()
    m = LruExpiringMap(3, 60.0, time_func=clock)
    assert m.get("missing") is None
    assert m.get("missing", "fallback") == "fallback"


def test_lru_expiring_map_ttl_expiry():
    clock = _FakeClock()
    m = LruExpiringMap(3, 60.0, time_func=clock)
    m.set("a", 1)
    clock.advance(30)
    assert m.get("a") == 1
    clock.advance(31)
    assert m.get("a") is None
    assert len(m) == 0


def test_lru_expiring_map_lru_eviction_when_full():
    clock = _FakeClock()
    m = LruExpiringMap(3, 60.0, time_func=clock)
    m.set("a", 1)
    m.set("b", 2)
    m.set("c", 3)
    m.set("d", 4)
    assert m.get("a") is None
    assert m.get("b") == 2
    assert m.get("c") == 3
    assert m.get("d") == 4
    assert len(m) == 3


def test_lru_expiring_map_get_bumps_lru_order():
    clock = _FakeClock()
    m = LruExpiringMap(3, 60.0, time_func=clock)
    m.set("a", 1)
    m.set("b", 2)
    m.set("c", 3)
    m.get("a")
    m.set("d", 4)
    assert m.get("a") == 1
    assert m.get("b") is None
    assert m.get("c") == 3
    assert m.get("d") == 4


def test_lru_expiring_map_set_resets_ttl():
    clock = _FakeClock()
    m = LruExpiringMap(3, 60.0, time_func=clock)
    m.set("a", 1)
    clock.advance(50)
    m.set("a", 2)
    clock.advance(30)
    assert m.get("a") == 2


def test_lru_expiring_map_stale_evicted_before_lru():
    clock = _FakeClock()
    m = LruExpiringMap(max_keys=3, ttl_seconds=10.0, time_func=clock)
    m.set("a", 1)
    m.set("b", 2)
    clock.advance(11)
    m.set("c", 3)
    m.set("d", 4)
    assert len(m) == 2
    assert m.get("a") is None
    assert m.get("b") is None


def test_lru_expiring_map_validates_args():
    with pytest.raises(ValueError):
        LruExpiringMap(0, 60.0)
    with pytest.raises(ValueError):
        LruExpiringMap(3, 0)
    with pytest.raises(ValueError):
        LruExpiringMap(-1, 60.0)
    with pytest.raises(ValueError):
        LruExpiringMap(3, -1.0)


@pytest.mark.asyncio
async def test_safe_get_or_set_returns_cached_value_on_hit():
    cache = SimpleCacheManager()
    cache.get = AsyncMock(return_value={"v": 1})
    cache.set = AsyncMock()
    loader = AsyncMock()
    result = await cache.safe_get_or_set(CacheNamespace.TEMP, "k", loader, ttl=60)
    assert result == {"v": 1}
    loader.assert_not_awaited()
    cache.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_get_or_set_calls_loader_on_miss_and_writes():
    cache = SimpleCacheManager()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    loader = AsyncMock(return_value={"v": 2})
    result = await cache.safe_get_or_set(CacheNamespace.TEMP, "k2", loader, ttl=60)
    assert result == {"v": 2}
    loader.assert_awaited_once()
    cache.set.assert_awaited_once_with(CacheNamespace.TEMP, "k2", {"v": 2}, ttl=60)


@pytest.mark.asyncio
async def test_safe_get_or_set_swallows_get_exception():
    cache = SimpleCacheManager()
    cache.get = AsyncMock(side_effect=RuntimeError("redis down"))
    cache.set = AsyncMock()
    loader = AsyncMock(return_value={"v": 3})
    result = await cache.safe_get_or_set(CacheNamespace.TEMP, "k3", loader, ttl=60)
    assert result == {"v": 3}
    loader.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_get_or_set_swallows_set_exception():
    cache = SimpleCacheManager()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(side_effect=RuntimeError("redis down"))
    loader = AsyncMock(return_value={"v": 4})
    result = await cache.safe_get_or_set(CacheNamespace.TEMP, "k4", loader, ttl=60)
    assert result == {"v": 4}


@pytest.mark.asyncio
async def test_safe_get_or_set_does_not_cache_none():
    cache = SimpleCacheManager()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    loader = AsyncMock(return_value=None)
    result = await cache.safe_get_or_set(CacheNamespace.TEMP, "k5", loader, ttl=60)
    assert result is None
    cache.set.assert_not_awaited()
