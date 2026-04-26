"""Shared test fixtures for nextreel-lite test suite."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import Quart


# ---------------------------------------------------------------------------
# Quart test app
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    """Minimal Quart app for unit tests that need a request/session context."""
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    return app


# ---------------------------------------------------------------------------
# Mock Redis client
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory fake that mirrors the subset of aioredis.Redis we use."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    async def ping(self):
        return True

    async def get(self, key: str):
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value
        self._ttls[key] = ttl

    async def set(self, key: str, value: str, ex: int | None = None):
        self._store[key] = value
        if ex:
            self._ttls[key] = ex

    async def delete(self, *keys: str):
        for key in keys:
            self._store.pop(key, None)
            self._ttls.pop(key, None)

    async def incr(self, key: str):
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    async def expire(self, key: str, ttl: int):
        self._ttls[key] = ttl

    async def aclose(self):
        self._store.clear()


@pytest.fixture
def fake_redis():
    return FakeRedis()


# ---------------------------------------------------------------------------
# Cache stub (for movie navigator tests)
# ---------------------------------------------------------------------------


class CacheStub:
    """Simple cache stub that returns a fixed payload on get()."""

    def __init__(self, payload=None):
        self._store: dict[str, object] = {}
        self.payload = payload

    def _make_key(self, namespace, key):
        """Form a cache key, handling both string and enum namespaces."""
        ns = namespace.value if hasattr(namespace, "value") else namespace
        return f"{ns}:{key}"

    async def get(self, namespace, key):
        if self.payload is not None:
            return self.payload
        return self._store.get(self._make_key(namespace, key))

    async def set(self, namespace, key, value, ttl=None):
        self._store[self._make_key(namespace, key)] = value

    async def delete(self, namespace, key):
        self._store.pop(self._make_key(namespace, key), None)

    async def safe_get_or_set(self, namespace, key, loader, ttl=None):
        """Get from cache, fall back to loader on miss, write back on hit."""
        try:
            cached = await self.get(namespace, key)
            if cached is not None:
                return cached
        except Exception:
            pass
        value = await loader()
        if value is not None:
            try:
                await self.set(namespace, key, value, ttl=ttl)
            except Exception:
                pass
        return value


@pytest.fixture
def cache_stub():
    return CacheStub()


# ---------------------------------------------------------------------------
# Mock DB pool
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_pool():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[])
    pool.get_metrics = AsyncMock(
        return_value={
            "pool_size": 10,
            "free_connections": 5,
            "circuit_breaker_state": "closed",
            "queries_executed": 100,
            "queries_failed": 0,
            "avg_query_time_ms": 12.5,
        }
    )
    pool.init_pool = AsyncMock()
    pool.close_pool = AsyncMock()

    # Mock the raw aiomysql pool path used by _execute_ddl for DDL statements.
    # _execute_ddl calls db_pool.pool.pool.acquire() / .release() directly.
    # The mock cursor's execute is exposed as pool._ddl_cursor for test assertions.
    ddl_cursor = AsyncMock()
    ddl_cursor.__aenter__ = AsyncMock(return_value=ddl_cursor)
    ddl_cursor.__aexit__ = AsyncMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=ddl_cursor)
    raw_pool = MagicMock()
    raw_pool.acquire = AsyncMock(return_value=mock_conn)
    raw_pool.release = MagicMock()
    pool.pool = MagicMock()
    pool.pool.pool = raw_pool
    pool._ddl_cursor = ddl_cursor
    return pool


def pytest_collection_modifyitems(config, items):
    """Keep spike-style resilience tests out of the default fast lane."""
    if os.environ.get("RUN_SPIKE") == "1":
        return

    skip_spike = pytest.mark.skip(reason="spike tests are opt-in; set RUN_SPIKE=1 to run them")
    for item in items:
        if "spike" in item.keywords:
            item.add_marker(skip_spike)
