"""Extended route tests — rate limiting, CSRF, logout, health/ready/metrics."""

import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from infra.rate_limit import (
    check_rate_limit_memory,
    _rate_limit_store,
    _RATE_LIMIT_MAX_KEYS,
    RATE_LIMIT_MAX,
    RATE_LIMIT_WINDOW,
)


# ---------------------------------------------------------------------------
# In-memory rate limiter
# ---------------------------------------------------------------------------


class TestRateLimitMemory:
    """check_rate_limit_memory — window, caps, and eviction."""

    @pytest.fixture(autouse=True)
    def clear_rate_limit_store(self):
        _rate_limit_store.clear()
        yield
        _rate_limit_store.clear()

    @pytest.mark.asyncio
    async def test_allows_under_limit(self, app):
        async with app.test_request_context("/", headers={"X-Forwarded-For": "1.2.3.4"}):
            for _ in range(RATE_LIMIT_MAX):
                assert await check_rate_limit_memory("test") is True

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self, app):
        async with app.test_request_context("/", headers={"X-Forwarded-For": "1.2.3.4"}):
            for _ in range(RATE_LIMIT_MAX):
                await check_rate_limit_memory("test")
            assert await check_rate_limit_memory("test") is False

    @pytest.mark.asyncio
    async def test_window_expiry_allows_again(self, app):
        async with app.test_request_context("/", headers={"X-Forwarded-For": "1.2.3.4"}):
            # Fill up the limit
            for _ in range(RATE_LIMIT_MAX):
                await check_rate_limit_memory("test")
            assert await check_rate_limit_memory("test") is False

            # Manually expire all timestamps
            for key in _rate_limit_store:
                _rate_limit_store[key] = [time.time() - RATE_LIMIT_WINDOW - 1]

            assert await check_rate_limit_memory("test") is True

    @pytest.mark.asyncio
    async def test_eviction_caps_key_count(self, app):
        """When key count exceeds _RATE_LIMIT_MAX_KEYS, stale keys are evicted."""
        # Stuff the store with stale entries
        stale_ts = time.time() - RATE_LIMIT_WINDOW - 10
        for i in range(_RATE_LIMIT_MAX_KEYS + 50):
            _rate_limit_store[f"old:{i}"] = [stale_ts]

        async with app.test_request_context("/", headers={"X-Forwarded-For": "9.9.9.9"}):
            await check_rate_limit_memory("new")

        # Stale keys should have been evicted
        assert len(_rate_limit_store) < _RATE_LIMIT_MAX_KEYS + 50

    @pytest.mark.asyncio
    async def test_different_endpoints_track_separately(self, app):
        async with app.test_request_context("/", headers={"X-Forwarded-For": "1.2.3.4"}):
            for _ in range(RATE_LIMIT_MAX):
                await check_rate_limit_memory("endpoint_a")
            # endpoint_a is exhausted
            assert await check_rate_limit_memory("endpoint_a") is False
            # endpoint_b should still be available
            assert await check_rate_limit_memory("endpoint_b") is True


# ---------------------------------------------------------------------------
# Health and operational endpoints (via test client)
# ---------------------------------------------------------------------------


from tests.helpers import TEST_ENV


class TestHealthEndpoint:
    async def test_health_returns_200(self):
        with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
            MockManager.return_value.home = AsyncMock(return_value="ok")

            from app import create_app

            app = create_app()
            app.config["TESTING"] = True

            async with app.app_context():
                client = app.test_client()
                response = await client.get("/health")
                assert response.status_code == 200
                data = await response.get_json()
                assert data["status"] == "healthy"


class TestOpsAuth:
    """Ops endpoints should require Bearer token when OPS_AUTH_TOKEN is set."""

    async def test_metrics_unauthorized_without_token(self):
        with patch("app.MovieManager") as MockManager, patch.dict(
            os.environ, {**TEST_ENV, "OPS_AUTH_TOKEN": "secret-token"}
        ):
            MockManager.return_value.home = AsyncMock(return_value={"default_backdrop_url": None})

            from app import create_app

            app = create_app()
            app.config["TESTING"] = True

            async with app.app_context():
                client = app.test_client()
                response = await client.get("/metrics")
                assert response.status_code == 401

    async def test_metrics_authorized_with_correct_token(self):
        with patch("app.MovieManager") as MockManager, patch.dict(
            os.environ, {**TEST_ENV, "OPS_AUTH_TOKEN": "secret-token"}
        ):
            MockManager.return_value.home = AsyncMock(return_value={"default_backdrop_url": None})

            from app import create_app

            app = create_app()
            app.config["TESTING"] = True

            async with app.app_context():
                client = app.test_client()
                response = await client.get(
                    "/metrics",
                    headers={"Authorization": "Bearer secret-token"},
                )
                assert response.status_code == 200

    async def test_ready_reports_component_status(self):
        with patch("app.MovieManager") as MockManager, patch.dict(
            os.environ, {**TEST_ENV, "OPS_AUTH_TOKEN": "secret-token"}
        ):
            manager = MockManager.return_value
            manager.home = AsyncMock(return_value={"default_backdrop_url": None})
            manager.db_pool.get_metrics = AsyncMock(
                return_value={
                    "pool_size": 2,
                    "free_connections": 1,
                    "circuit_breaker_state": "closed",
                    "queries_executed": 5,
                    "avg_query_time_ms": 3.2,
                }
            )
            manager.candidate_store.has_fresh_data = AsyncMock(return_value=True)
            manager.projection_store.ready_check = AsyncMock(return_value=True)

            from app import create_app

            app = create_app()
            app.config["TESTING"] = True
            app.navigation_state_store = AsyncMock()
            app.navigation_state_store.ready_check = AsyncMock(return_value=True)
            app.redis_available = True
            app.worker_available = False

            async with app.app_context():
                client = app.test_client()
                response = await client.get(
                    "/ready",
                    headers={"Authorization": "Bearer secret-token"},
                )
                data = await response.get_json()

            assert response.status_code == 200
            assert data["status"] == "ready"
            assert data["navigation_state"]["ready"] is True
            assert data["movie_candidates"]["fresh"] is True
            assert data["projection_generation"]["ready"] is True


# ---------------------------------------------------------------------------
# CSRF validation
# ---------------------------------------------------------------------------


class TestCSRFValidation:
    """CSRF token must match between session and form/header."""

    async def test_post_without_csrf_returns_403(self):
        with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
            manager = MockManager.return_value
            manager.filtered_movie = AsyncMock(return_value="ok")

            from app import create_app

            app = create_app()
            app.config["TESTING"] = True

            async with app.app_context():
                client = app.test_client()
                response = await client.post("/filtered_movie", data={"year_min": "2000"})
                assert response.status_code == 403

    async def test_get_next_movie_rejected_as_post_only(self):
        """GET to /next_movie returns 405 — route is POST-only."""
        with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
            manager = MockManager.return_value
            manager.next_movie = AsyncMock(return_value="next")

            from app import create_app

            app = create_app()
            app.config["TESTING"] = True

            async with app.app_context():
                client = app.test_client()
                response = await client.get("/next_movie")
                assert response.status_code == 405
