"""Tests for database/pool.py — DatabaseConnectionPool wrapper."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database.errors import DatabaseError


class TestDatabaseConnectionPool:
    """DatabaseConnectionPool wraps SecureConnectionPool with env-driven config."""

    def _make_pool(self, env_overrides=None):
        """Create a DatabaseConnectionPool with mocked SecureConnectionPool."""
        env = {
            "NEXTREEL_ENV": "development",
            "VALIDATE_SSL": "false",
        }
        if env_overrides:
            env.update(env_overrides)

        with patch.dict(os.environ, env), \
             patch("database.pool.SecureConnectionPool") as MockSecure:
            mock_instance = AsyncMock()
            mock_instance.init_pool = AsyncMock()
            mock_instance.close_pool = AsyncMock()
            mock_instance.execute_secure = AsyncMock(return_value=[{"tconst": "tt1"}])
            mock_instance.get_pool_status = AsyncMock(return_value={
                "pool_size": 10,
                "free_connections": 5,
                "circuit_breaker_state": "closed",
            })
            mock_instance.acquire = MagicMock()
            MockSecure.return_value = mock_instance

            from database.pool import DatabaseConnectionPool
            pool = DatabaseConnectionPool({
                "host": "localhost",
                "user": "test",
                "password": "pass",
                "database": "testdb",
            })
            return pool, mock_instance

    @pytest.mark.asyncio
    async def test_init_pool_delegates(self):
        pool, mock = self._make_pool()
        await pool.init_pool()
        mock.init_pool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_pool_delegates(self):
        pool, mock = self._make_pool()
        await pool.close_pool()
        mock.close_pool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_delegates_to_secure_pool(self):
        pool, mock = self._make_pool()
        result = await pool.execute("SELECT 1", [], fetch="one")
        mock.execute_secure.assert_awaited_once_with(
            "SELECT 1", [], user_id=None, fetch="one"
        )
        assert result == [{"tconst": "tt1"}]

    @pytest.mark.asyncio
    async def test_execute_wraps_generic_exception(self):
        pool, mock = self._make_pool()
        mock.execute_secure = AsyncMock(side_effect=ValueError("boom"))
        with pytest.raises(DatabaseError, match="Query failed"):
            await pool.execute("SELECT 1")

    @pytest.mark.asyncio
    async def test_execute_passes_through_database_error(self):
        pool, mock = self._make_pool()
        mock.execute_secure = AsyncMock(side_effect=DatabaseError("db down"))
        with pytest.raises(DatabaseError, match="db down"):
            await pool.execute("SELECT 1")

    @pytest.mark.asyncio
    async def test_get_metrics_delegates(self):
        pool, mock = self._make_pool()
        metrics = await pool.get_metrics()
        assert metrics["pool_size"] == 10
        assert metrics["circuit_breaker_state"] == "closed"

    def test_ssl_enabled_in_production(self):
        pool, _ = self._make_pool({
            "NEXTREEL_ENV": "production",
            "VALIDATE_SSL": "true",
        })
        assert pool.secure_config.validate_ssl is True

    def test_ssl_disabled_in_development(self):
        pool, _ = self._make_pool({
            "NEXTREEL_ENV": "development",
            "VALIDATE_SSL": "false",
        })
        assert pool.secure_config.validate_ssl is False

    def test_pool_sizes_from_env(self):
        pool, _ = self._make_pool({
            "POOL_MIN_SIZE": "3",
            "POOL_MAX_SIZE": "15",
        })
        assert pool.secure_config.min_size == 3
        assert pool.secure_config.max_size == 15

    def test_repr(self):
        pool, _ = self._make_pool()
        r = repr(pool)
        assert "localhost" in r
        assert "testdb" in r
