import logging

import pytest
from quart import Quart, g

from middleware import add_correlation_id, _CORRELATION_LOG_SKIP_PREFIXES


def _make_app():
    app = Quart(__name__)
    app.before_request(add_correlation_id)

    @app.route("/")
    async def index():
        return g.correlation_id

    @app.route("/metrics")
    async def metrics():
        return g.correlation_id

    @app.route("/health")
    async def health():
        return g.correlation_id

    return app


@pytest.mark.asyncio
async def test_correlation_log_emitted_for_root(caplog):
    app = _make_app()
    client = app.test_client()
    with caplog.at_level(logging.INFO, logger="middleware"):
        resp = await client.get("/")
        assert resp.status_code == 200
        body = await resp.get_data()
        assert body  # correlation id was set
    assert any("New request received" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_correlation_log_skipped_for_metrics_path(caplog):
    app = _make_app()
    client = app.test_client()
    with caplog.at_level(logging.INFO, logger="middleware"):
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        body = await resp.get_data()
        assert body  # correlation id still set
    assert not any("New request received" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_correlation_log_skipped_for_health_path(caplog):
    app = _make_app()
    client = app.test_client()
    with caplog.at_level(logging.INFO, logger="middleware"):
        resp = await client.get("/health")
        assert resp.status_code == 200
    assert not any("New request received" in r.message for r in caplog.records)


def test_skip_prefixes_cover_expected_paths():
    expected = ("/static", "/favicon.ico", "/health", "/ready", "/metrics")
    for path in expected:
        assert path.startswith(_CORRELATION_LOG_SKIP_PREFIXES)
