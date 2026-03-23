"""Tests for SimpleCacheManager — connection modes, get/set/delete, error handling."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simple_cache import CacheNamespace, SimpleCacheManager


# ---------------------------------------------------------------------------
# Initialization modes
# ---------------------------------------------------------------------------


class TestInitialization:
    """SimpleCacheManager supports three connection modes."""

    @pytest.mark.asyncio
    async def test_init_with_shared_redis_client(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis)
        await cache.initialize()
        assert cache._redis is fake_redis
        assert cache._owns_connection is False

    @pytest.mark.asyncio
    async def test_init_with_connection_pool(self):
        mock_pool = MagicMock()
        with patch("simple_cache.aioredis.Redis") as MockRedis:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            MockRedis.return_value = mock_client

            cache = SimpleCacheManager(connection_pool=mock_pool)
            await cache.initialize()
            assert cache._redis is mock_client
            assert cache._owns_connection is False

    @pytest.mark.asyncio
    async def test_init_with_redis_url(self):
        with patch("simple_cache.aioredis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_from_url.return_value = mock_client

            cache = SimpleCacheManager(redis_url="redis://localhost:6379")
            await cache.initialize()
            assert cache._redis is mock_client
            assert cache._owns_connection is True

    @pytest.mark.asyncio
    async def test_init_with_no_args_leaves_redis_none(self):
        cache = SimpleCacheManager()
        await cache.initialize()
        assert cache._redis is None

    @pytest.mark.asyncio
    async def test_init_shared_client_ping_failure_sets_none(self):
        bad_client = AsyncMock()
        bad_client.ping = AsyncMock(side_effect=ConnectionError("down"))
        cache = SimpleCacheManager(redis_client=bad_client)
        await cache.initialize()
        assert cache._redis is None

    @pytest.mark.asyncio
    async def test_init_pool_ping_failure_sets_none(self):
        mock_pool = MagicMock()
        with patch("simple_cache.aioredis.Redis") as MockRedis:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=ConnectionError("down"))
            MockRedis.return_value = mock_client

            cache = SimpleCacheManager(connection_pool=mock_pool)
            await cache.initialize()
            assert cache._redis is None

    @pytest.mark.asyncio
    async def test_init_url_failure_sets_none(self):
        with patch("simple_cache.aioredis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=ConnectionError("down"))
            mock_from_url.return_value = mock_client

            cache = SimpleCacheManager(redis_url="redis://localhost:6379")
            await cache.initialize()
            assert cache._redis is None


# ---------------------------------------------------------------------------
# Close behavior
# ---------------------------------------------------------------------------


class TestClose:
    """close() should only disconnect owned connections."""

    @pytest.mark.asyncio
    async def test_close_owned_connection(self):
        with patch("simple_cache.aioredis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_client.aclose = AsyncMock()
            mock_from_url.return_value = mock_client

            cache = SimpleCacheManager(redis_url="redis://localhost:6379")
            await cache.initialize()
            assert cache._owns_connection is True
            await cache.close()
            mock_client.aclose.assert_awaited_once()
            assert cache._redis is None

    @pytest.mark.asyncio
    async def test_close_shared_connection_does_not_disconnect(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis)
        await cache.initialize()
        await cache.close()
        # fake_redis should NOT be closed (we don't own it)
        # After close, _redis should be None
        assert cache._redis is None

    @pytest.mark.asyncio
    async def test_close_when_no_connection(self):
        cache = SimpleCacheManager()
        await cache.initialize()
        # Should not raise
        await cache.close()


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------


class TestMakeKey:
    def test_key_format(self):
        cache = SimpleCacheManager()
        key = cache._make_key(CacheNamespace.MOVIE, "tt1234567")
        assert key == "cache:movie:tt1234567"

    def test_all_namespaces(self):
        cache = SimpleCacheManager()
        for ns in CacheNamespace:
            key = cache._make_key(ns, "test")
            assert key == f"cache:{ns.value}:test"


# ---------------------------------------------------------------------------
# Get / Set / Delete operations
# ---------------------------------------------------------------------------


class TestGetSetDelete:
    @pytest.mark.asyncio
    async def test_set_then_get(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis)
        await cache.initialize()

        await cache.set(CacheNamespace.MOVIE, "tt123", {"title": "Test"})
        result = await cache.get(CacheNamespace.MOVIE, "tt123")
        assert result == {"title": "Test"}

    @pytest.mark.asyncio
    async def test_get_returns_none_on_miss(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis)
        await cache.initialize()

        result = await cache.get(CacheNamespace.MOVIE, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_removes_key(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis)
        await cache.initialize()

        await cache.set(CacheNamespace.MOVIE, "tt123", {"title": "Test"})
        await cache.delete(CacheNamespace.MOVIE, "tt123")
        result = await cache.get(CacheNamespace.MOVIE, "tt123")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_uses_default_ttl(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis, default_ttl=300)
        await cache.initialize()

        await cache.set(CacheNamespace.API, "key", "value")
        assert fake_redis._ttls["cache:api:key"] == 300

    @pytest.mark.asyncio
    async def test_set_with_custom_ttl(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis, default_ttl=300)
        await cache.initialize()

        await cache.set(CacheNamespace.API, "key", "value", ttl=60)
        assert fake_redis._ttls["cache:api:key"] == 60

    @pytest.mark.asyncio
    async def test_json_serialization_round_trip(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis)
        await cache.initialize()

        data = {"nested": {"list": [1, 2, 3], "flag": True}}
        await cache.set(CacheNamespace.TEMP, "complex", data)
        result = await cache.get(CacheNamespace.TEMP, "complex")
        assert result == data


# ---------------------------------------------------------------------------
# Graceful error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_redis(self):
        cache = SimpleCacheManager()
        await cache.initialize()
        assert cache._redis is None
        result = await cache.get(CacheNamespace.MOVIE, "key")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_silently_fails_when_no_redis(self):
        cache = SimpleCacheManager()
        await cache.initialize()
        # Should not raise
        await cache.set(CacheNamespace.MOVIE, "key", "value")

    @pytest.mark.asyncio
    async def test_delete_silently_fails_when_no_redis(self):
        cache = SimpleCacheManager()
        await cache.initialize()
        # Should not raise
        await cache.delete(CacheNamespace.MOVIE, "key")

    @pytest.mark.asyncio
    async def test_get_returns_none_on_redis_error(self):
        bad_client = AsyncMock()
        bad_client.ping = AsyncMock()
        bad_client.get = AsyncMock(side_effect=ConnectionError("timeout"))
        cache = SimpleCacheManager(redis_client=bad_client)
        await cache.initialize()
        result = await cache.get(CacheNamespace.MOVIE, "key")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_swallows_redis_error(self):
        bad_client = AsyncMock()
        bad_client.ping = AsyncMock()
        bad_client.setex = AsyncMock(side_effect=ConnectionError("timeout"))
        cache = SimpleCacheManager(redis_client=bad_client)
        await cache.initialize()
        # Should not raise
        await cache.set(CacheNamespace.MOVIE, "key", "value")

    @pytest.mark.asyncio
    async def test_get_returns_none_on_json_decode_error(self, fake_redis):
        cache = SimpleCacheManager(redis_client=fake_redis)
        await cache.initialize()
        # Inject invalid JSON directly
        fake_redis._store["cache:movie:bad"] = "not-valid-json{{"
        result = await cache.get(CacheNamespace.MOVIE, "bad")
        assert result is None


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    @pytest.mark.asyncio
    async def test_extra_kwargs_ignored(self, fake_redis):
        """SecureCacheManager kwargs should be silently ignored."""
        cache = SimpleCacheManager(
            redis_client=fake_redis,
            secret_key="should-be-ignored",
            enable_monitoring=True,
        )
        await cache.initialize()
        assert cache._redis is fake_redis
