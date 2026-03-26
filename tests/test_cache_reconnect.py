"""Tests for SimpleCacheManager lazy reconnect and error recovery.

Targets:
  1. _try_reconnect() attempts lazy reconnect when Redis dies
  2. Error on get/set/delete nulls _redis (triggers future reconnect)
  3. No reconnect loop without connection info
  4. Reconnect without backoff (performance concern — documents the behavior)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infra.cache import CacheNamespace, SimpleCacheManager


class TestLazyReconnect:
    @pytest.mark.asyncio
    async def test_get_after_connection_loss_attempts_reconnect(self):
        """After Redis error nulls _redis, next get() should try reconnect."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("lost"))

        cache = SimpleCacheManager(redis_url="redis://localhost:6379", redis_client=mock_client)
        await cache.initialize()
        assert cache._redis is mock_client

        # First get fails → _redis set to None
        await cache.get(CacheNamespace.MOVIE, "tt1")
        assert cache._redis is None

        # _try_reconnect calls initialize() which uses redis_url
        # Verify the reconnect path is exercised
        with patch("infra.cache.aioredis.from_url") as mock_from_url:
            new_client = AsyncMock()
            new_client.ping = AsyncMock()
            new_client.get = AsyncMock(return_value=None)
            mock_from_url.return_value = new_client

            result = await cache.get(CacheNamespace.MOVIE, "tt1")

        # Should have attempted reconnect via from_url
        mock_from_url.assert_called_once()
        assert result is None

    @pytest.mark.asyncio
    async def test_set_after_error_nulls_redis(self):
        """Redis error on set should null _redis for future reconnect."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client.setex = AsyncMock(side_effect=ConnectionError("broken"))

        cache = SimpleCacheManager(redis_client=mock_client)
        await cache.initialize()
        assert cache._redis is mock_client

        await cache.set(CacheNamespace.MOVIE, "tt1", {"data": 1})
        assert cache._redis is None

    @pytest.mark.asyncio
    async def test_delete_after_error_nulls_redis(self):
        """Redis error on delete should null _redis for future reconnect."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=ConnectionError("broken"))

        cache = SimpleCacheManager(redis_client=mock_client)
        await cache.initialize()

        await cache.delete(CacheNamespace.MOVIE, "tt1")
        assert cache._redis is None

    @pytest.mark.asyncio
    async def test_no_reconnect_without_connection_info(self):
        """Without redis_url or connection_pool, reconnect should not be attempted."""
        cache = SimpleCacheManager()  # No connection info
        await cache.initialize()
        assert cache._redis is None

        # _try_reconnect should return False without doing anything
        result = await cache._try_reconnect()
        assert result is False

    @pytest.mark.asyncio
    async def test_reconnect_succeeds_with_redis_url(self):
        """With redis_url, reconnect should work after failure."""
        with patch("infra.cache.aioredis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock()
            mock_client.get = AsyncMock(return_value=None)
            mock_from_url.return_value = mock_client

            cache = SimpleCacheManager(redis_url="redis://localhost:6379")
            await cache.initialize()

            # Simulate failure
            cache._redis = None

            # Try reconnect
            result = await cache._try_reconnect()
            assert result is True
            assert cache._redis is not None

    @pytest.mark.asyncio
    async def test_reconnect_failure_returns_false(self):
        """Failed reconnect should return False, not raise."""
        with patch("infra.cache.aioredis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=ConnectionError("still down"))
            mock_from_url.return_value = mock_client

            cache = SimpleCacheManager(redis_url="redis://localhost:6379")
            # First init fails
            await cache.initialize()
            assert cache._redis is None

            result = await cache._try_reconnect()
            assert result is False
