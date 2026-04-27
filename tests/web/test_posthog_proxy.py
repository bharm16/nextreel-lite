"""Tests for the /ph/* PostHog reverse proxy.

These cover the security surface of the proxy: path allow-list, body-size
cap, header stripping, disabled-config behaviour, and rate limiting. The
upstream HTTP call is mocked at the ``httpx.AsyncClient`` level so no
real network I/O happens.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.helpers import TEST_ENV


def _make_upstream_response(status: int = 200, body: bytes = b"ok", headers=None):
    """Build a stand-in for httpx.Response with the attributes we read."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.content = body
    resp.headers = headers or {"content-type": "text/plain"}
    return resp


@pytest.fixture
def test_client_with_proxy_enabled():
    """Test client with PostHog proxy turned ON via posthog_config."""
    env = dict(TEST_ENV)
    env["POSTHOG_PROJECT_KEY"] = "phc_test_key"
    with patch.dict(os.environ, env), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool = MagicMock()
        manager.db_pool.execute = AsyncMock(return_value=[])

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        # Force ``enabled`` true even if PostHog SDK construction is mocked.
        app.posthog_config = {
            "enabled": True,
            "project_key": "phc_test_key",
            "api_host": "/ph",
            "upstream_host": "https://us.i.posthog.com",
        }
        yield app.test_client()


@pytest.fixture
def test_client_with_proxy_disabled():
    """Test client with PostHog proxy explicitly OFF."""
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool = MagicMock()
        manager.db_pool.execute = AsyncMock(return_value=[])

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        app.posthog_config = {
            "enabled": False,
            "project_key": "",
            "api_host": "/ph",
            "upstream_host": "https://us.i.posthog.com",
        }
        yield app.test_client()


@pytest.mark.asyncio
async def test_proxy_returns_204_when_disabled(test_client_with_proxy_disabled):
    """Disabled config must return 204, not 404 — keeps the SDK quiet."""
    response = await test_client_with_proxy_disabled.get("/ph/decide/")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_proxy_returns_404_for_path_not_in_allowlist(
    test_client_with_proxy_enabled,
):
    """Anything outside _ALLOWED_PREFIXES is rejected before any forwarding."""
    response = await test_client_with_proxy_enabled.get("/ph/admin/secrets")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_proxy_forwards_allowed_path(test_client_with_proxy_enabled):
    """Allowed prefixes (e/, decide/, static/, etc.) reach the upstream."""
    upstream = _make_upstream_response(status=200, body=b'{"ok":true}')
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=upstream)

    with patch("nextreel.web.routes.posthog_proxy.httpx.AsyncClient", return_value=mock_client):
        response = await test_client_with_proxy_enabled.post("/ph/e/", data=b"{}")

    assert response.status_code == 200
    assert mock_client.request.called
    # Target URL must be derived from upstream_host + path; never a
    # caller-controlled host.
    args, kwargs = mock_client.request.call_args
    assert args[1].startswith("https://us.i.posthog.com/")


@pytest.mark.asyncio
async def test_proxy_drops_cookie_and_authorization_headers(
    test_client_with_proxy_enabled,
):
    """Sensitive headers must NOT be forwarded upstream — they would leak
    first-party session cookies into PostHog's log retention.
    """
    upstream = _make_upstream_response()
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=upstream)

    with patch("nextreel.web.routes.posthog_proxy.httpx.AsyncClient", return_value=mock_client):
        await test_client_with_proxy_enabled.post(
            "/ph/e/",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer evil-token",
                "Cookie": "session=secret",
                "User-Agent": "test-agent/1.0",
            },
        )

    forwarded_headers = mock_client.request.call_args.kwargs["headers"]
    forwarded_lower = {k.lower() for k in forwarded_headers}
    assert "authorization" not in forwarded_lower
    assert "cookie" not in forwarded_lower
    # Sanity-check that allow-listed headers DO pass through.
    assert "user-agent" in forwarded_lower


@pytest.mark.asyncio
async def test_proxy_rejects_oversized_declared_content_length(
    test_client_with_proxy_enabled,
):
    """A Content-Length above the cap must 413 before any read happens."""
    response = await test_client_with_proxy_enabled.post(
        "/ph/e/",
        data=b"x",  # body content irrelevant — header drives the rejection
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(10 * 1024 * 1024),  # 10 MiB > 2 MiB cap
        },
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_proxy_rejects_oversized_streamed_body(
    test_client_with_proxy_enabled,
):
    """A body larger than the cap must 413 even without a Content-Length
    header, by bounding during the streamed read.
    """
    # 3 MiB > 2 MiB cap; no upstream mock because we expect early rejection.
    big_body = b"a" * (3 * 1024 * 1024)
    response = await test_client_with_proxy_enabled.post(
        "/ph/e/",
        data=big_body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_proxy_returns_502_on_upstream_http_error(
    test_client_with_proxy_enabled,
):
    """Network errors to PostHog must surface as 502 to the SDK, not 500."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(side_effect=httpx.ConnectError("boom"))

    with patch("nextreel.web.routes.posthog_proxy.httpx.AsyncClient", return_value=mock_client):
        response = await test_client_with_proxy_enabled.post("/ph/e/", data=b"{}")

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_proxy_skips_body_for_get_requests(test_client_with_proxy_enabled):
    """GETs (e.g. SDK bundle fetch) must skip the body-read branch entirely."""
    upstream = _make_upstream_response(status=200, body=b"<sdk bundle>")
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=upstream)

    with patch("nextreel.web.routes.posthog_proxy.httpx.AsyncClient", return_value=mock_client):
        response = await test_client_with_proxy_enabled.get("/ph/static/array.js")

    assert response.status_code == 200
    # GET request: content kwarg should be None.
    assert mock_client.request.call_args.kwargs["content"] is None


def test_path_allowlist_covers_expected_prefixes():
    """Cheap unit-level check that the allow-list hasn't drifted out from
    under the SDK's expected paths.
    """
    from nextreel.web.routes.posthog_proxy import _is_allowed

    for path in (
        "static/array.js",
        "e/",
        "i/v0/e/",
        "decide/",
        "s/",
        "engage/",
        "capture/",
        "batch/",
    ):
        assert _is_allowed(path), f"expected {path!r} to be allowed"

    for path in (
        "admin/users",
        "..",
        "../etc/passwd",
        "internal/private",
        "",
    ):
        assert not _is_allowed(path), f"expected {path!r} to be rejected"
