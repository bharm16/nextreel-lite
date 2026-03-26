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
    def test_health_returns_200(self):
        import asyncio

        async def run():
            with patch.dict(os.environ, TEST_ENV), \
                 patch("app.MovieManager") as MockManager:
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

        asyncio.run(run())


class TestOpsAuth:
    """Ops endpoints should require Bearer token when OPS_AUTH_TOKEN is set."""

    def test_metrics_unauthorized_without_token(self):
        import asyncio

        async def run():
            with patch("app.MovieManager") as MockManager, \
                 patch.dict(os.environ, {**TEST_ENV, "OPS_AUTH_TOKEN": "secret-token"}):
                MockManager.return_value.home = AsyncMock(return_value={"default_backdrop_url": None})

                from app import create_app
                app = create_app()
                app.config["TESTING"] = True

                async with app.app_context():
                    client = app.test_client()
                    response = await client.get("/metrics")
                    assert response.status_code == 401

        asyncio.run(run())

    def test_metrics_authorized_with_correct_token(self):
        import asyncio

        async def run():
            with patch("app.MovieManager") as MockManager, \
                 patch.dict(os.environ, {**TEST_ENV, "OPS_AUTH_TOKEN": "secret-token"}):
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
                    # Should get past auth (200 from metrics endpoint)
                    assert response.status_code == 200

        asyncio.run(run())


# ---------------------------------------------------------------------------
# CSRF validation
# ---------------------------------------------------------------------------


class TestCSRFValidation:
    """CSRF token must match between session and form/header."""

    def test_post_without_csrf_returns_403(self):
        import asyncio

        async def run():
            with patch.dict(os.environ, TEST_ENV), \
                 patch("app.MovieManager") as MockManager:
                manager = MockManager.return_value
                manager.filtered_movie = AsyncMock(return_value="ok")

                from app import create_app
                app = create_app()
                app.config["TESTING"] = True

                async with app.app_context():
                    client = app.test_client()
                    response = await client.post(
                        "/filtered_movie", data={"year_min": "2000"}
                    )
                    assert response.status_code == 403

        asyncio.run(run())

    def test_get_next_movie_rejected_as_post_only(self):
        """GET to /next_movie returns 405 — route is POST-only."""
        import asyncio

        async def run():
            with patch.dict(os.environ, TEST_ENV), \
                 patch("app.MovieManager") as MockManager:
                manager = MockManager.return_value
                manager.next_movie = AsyncMock(return_value="next")

                from app import create_app
                app = create_app()
                app.config["TESTING"] = True

                async with app.app_context():
                    client = app.test_client()
                    response = await client.get("/next_movie")
                    assert response.status_code == 405

        asyncio.run(run())
