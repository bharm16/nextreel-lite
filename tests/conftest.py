"""Shared test fixtures for nextreel-lite test suite."""

import asyncio
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

    async def get(self, namespace, key):
        if self.payload is not None:
            return self.payload
        return self._store.get(f"{namespace}:{key}")

    async def set(self, namespace, key, value, ttl=None):
        self._store[f"{namespace}:{key}"] = value

    async def delete(self, namespace, key):
        self._store.pop(f"{namespace}:{key}", None)


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
    return pool
