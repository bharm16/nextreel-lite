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


async def test_with_timeout_does_not_let_handler_complete_after_504(app):
    """The inner handler must not run to completion after a 504 has been
    sent to the client. The previous ``shield(task)`` + ``task.cancel()``
    pattern was contradictory; the simpler ``wait_for(fn(...), timeout)``
    cancels cleanly without a window for late writes.
    """
    from infra.route_helpers import with_timeout

    completed_normally = {"value": False}

    @app.route("/slow")
    @with_timeout(1)  # 1 second budget
    async def slow():
        await asyncio.sleep(0)  # let timeout schedule
        await asyncio.sleep(10)  # well past the 1s budget
        completed_normally["value"] = True
        return "should-not-happen"

    # Patch wait_for to a much smaller value so the test runs quickly.
    original_wait_for = asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, timeout=0.05)

    with patch("infra.route_helpers.asyncio.wait_for", fast_wait_for):
        async with app.test_request_context("/slow"):
            client = app.test_client()
            response = await client.get("/slow")
    assert response.status_code == 504
    assert completed_normally["value"] is False
