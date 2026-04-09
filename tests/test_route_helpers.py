"""Tests for route decorator helpers."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest
from quart import Quart

from tests.helpers import TEST_ENV


@pytest.fixture
def app():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test"
    app.config["TESTING"] = True
    return app


async def test_with_timeout_returns_504_on_timeout(app):
    from infra.route_helpers import with_timeout

    @app.route("/slow")
    @with_timeout(0)  # 0 seconds = immediate timeout
    async def slow():
        await asyncio.sleep(10)
        return "ok"

    async with app.test_request_context("/slow"):
        client = app.test_client()
        response = await client.get("/slow")
        assert response.status_code == 504


async def test_with_timeout_passes_through_on_success(app):
    from infra.route_helpers import with_timeout

    @app.route("/fast")
    @with_timeout(5)
    async def fast():
        return "ok"

    async with app.test_request_context("/fast"):
        client = app.test_client()
        response = await client.get("/fast")
        assert response.status_code == 200


async def test_rate_limited_returns_429(app):
    from infra.route_helpers import rate_limited

    @app.route("/limited")
    @rate_limited("test")
    async def limited():
        return "ok"

    with patch("infra.route_helpers.check_rate_limit", AsyncMock(return_value=False)):
        async with app.test_request_context("/limited"):
            client = app.test_client()
            response = await client.get("/limited")
            assert response.status_code == 429


async def test_rate_limited_allows_through(app):
    from infra.route_helpers import rate_limited

    @app.route("/allowed")
    @rate_limited("test")
    async def allowed():
        return "ok"

    with patch("infra.route_helpers.check_rate_limit", AsyncMock(return_value=True)):
        async with app.test_request_context("/allowed"):
            client = app.test_client()
            response = await client.get("/allowed")
            assert response.status_code == 200


async def test_with_timeout_awaits_cancelled_task(app):
    """with_timeout must fully await cancellation so connections don't leak."""
    from infra.route_helpers import with_timeout

    task_ref = {}

    async def slow_inner():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise
        return "never"

    @app.route("/slow")
    @with_timeout(seconds=0)
    async def slow_route():
        task = asyncio.ensure_future(slow_inner())
        task_ref["task"] = task
        return await task

    async with app.test_request_context("/slow"):
        client = app.test_client()
        response = await client.get("/slow")
        assert response.status_code == 504
