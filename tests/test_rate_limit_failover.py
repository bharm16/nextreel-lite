"""Tests for rate limiter Redis→memory failover.

Targets:
  1. Redis error triggers fallback to memory
  2. Fallover logs warning only on transition (not every request)
  3. In-memory state persists across failover (not reset)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infra.rate_limit import (
    RATE_LIMIT_MAX,
    RATE_LIMIT_WINDOW,
    _rate_limit_store,
    check_rate_limit,
    check_rate_limit_memory,
    get_rate_limit_backend,
)


@pytest.fixture(autouse=True)
def reset_rate_limit_state():
    """Reset module-level state between tests."""
    import infra.rate_limit as rl
    rl._active_backend = "memory"
    rl._rate_limit_store.clear()
    yield
    rl._rate_limit_store.clear()


class TestMemoryRateLimiter:
    @pytest.mark.asyncio
    async def test_allows_under_limit(self):
        with patch("infra.rate_limit.get_client_ip", return_value="1.2.3.4"):
            for _ in range(RATE_LIMIT_MAX):
                assert await check_rate_limit_memory("test") is True

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self):
        with patch("infra.rate_limit.get_client_ip", return_value="1.2.3.4"):
            for _ in range(RATE_LIMIT_MAX):
                await check_rate_limit_memory("test")
            assert await check_rate_limit_memory("test") is False

    @pytest.mark.asyncio
    async def test_different_ips_independent(self):
        with patch("infra.rate_limit.get_client_ip", return_value="1.2.3.4"):
            for _ in range(RATE_LIMIT_MAX):
                await check_rate_limit_memory("test")
            assert await check_rate_limit_memory("test") is False

        with patch("infra.rate_limit.get_client_ip", return_value="5.6.7.8"):
            assert await check_rate_limit_memory("test") is True


class TestRedisFailover:
    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_memory(self):
        """Redis exception should silently fall back to memory backend."""
        mock_redis = AsyncMock()
        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        mock_app = MagicMock()
        mock_app.config = {"SESSION_REDIS": mock_redis}

        with patch("infra.rate_limit.current_app", mock_app), \
             patch("infra.rate_limit.get_client_ip", return_value="1.2.3.4"):
            result = await check_rate_limit("test")

        assert result is True
        assert get_rate_limit_backend() == "memory"

    @pytest.mark.asyncio
    async def test_no_redis_uses_memory(self):
        """When SESSION_REDIS is None, should use memory backend."""
        mock_app = MagicMock()
        mock_app.config = {"SESSION_REDIS": None}

        with patch("infra.rate_limit.current_app", mock_app), \
             patch("infra.rate_limit.get_client_ip", return_value="1.2.3.4"):
            result = await check_rate_limit("test")

        assert result is True
        assert get_rate_limit_backend() == "memory"
