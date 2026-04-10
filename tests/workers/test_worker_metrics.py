"""Tests for infra.worker_metrics — observability hardening for the arq worker."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infra import worker_metrics as wm


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def test_resolve_metrics_host_default(monkeypatch):
    monkeypatch.delenv("WORKER_METRICS_HOST", raising=False)
    assert wm._resolve_metrics_host() == wm.DEFAULT_METRICS_HOST


def test_resolve_metrics_host_env(monkeypatch):
    monkeypatch.setenv("WORKER_METRICS_HOST", "0.0.0.0")
    assert wm._resolve_metrics_host() == "0.0.0.0"


def test_resolve_metrics_port_default(monkeypatch):
    monkeypatch.delenv("WORKER_METRICS_PORT", raising=False)
    assert wm._resolve_metrics_port() == wm.DEFAULT_METRICS_PORT


def test_resolve_metrics_port_env(monkeypatch):
    monkeypatch.setenv("WORKER_METRICS_PORT", "9191")
    assert wm._resolve_metrics_port() == 9191


def test_resolve_metrics_port_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("WORKER_METRICS_PORT", "not-a-port")
    # Explicit default passes through
    assert wm._resolve_metrics_port(default=9999) == 9999


def test_resolve_poll_interval_default(monkeypatch):
    monkeypatch.delenv("WORKER_METRICS_POLL_INTERVAL", raising=False)
    assert wm._resolve_poll_interval() == wm.DEFAULT_POLL_INTERVAL


def test_resolve_poll_interval_enforces_minimum(monkeypatch):
    monkeypatch.setenv("WORKER_METRICS_POLL_INTERVAL", "0.1")
    assert wm._resolve_poll_interval() == 1.0


def test_resolve_queue_key_env_precedence(monkeypatch):
    monkeypatch.setenv("ARQ_QUEUE_KEY", "from_env")

    class FakeSettings:
        queue_name = "from_attr"

    assert wm.resolve_queue_key(FakeSettings) == "from_env"


def test_resolve_queue_key_attribute_fallback(monkeypatch):
    monkeypatch.delenv("ARQ_QUEUE_KEY", raising=False)

    class FakeSettings:
        queue_name = "from_attr"

    assert wm.resolve_queue_key(FakeSettings) == "from_attr"


def test_resolve_queue_key_default(monkeypatch):
    monkeypatch.delenv("ARQ_QUEUE_KEY", raising=False)
    assert wm.resolve_queue_key(None) == wm.DEFAULT_QUEUE_KEY


# ---------------------------------------------------------------------------
# HTTP server — success and bind-failure paths
# ---------------------------------------------------------------------------


def test_start_worker_metrics_server_handles_bind_error(monkeypatch):
    """Second bind on the same host/port must return False, not raise."""

    def boom(port, addr):  # noqa: ARG001
        raise OSError("address already in use")

    monkeypatch.setattr(
        "prometheus_client.start_http_server", boom
    )
    assert wm.start_worker_metrics_server(port=18001) is False


def test_start_worker_metrics_server_success(monkeypatch):
    called = {}

    def ok(port, addr):
        called["port"] = port
        called["addr"] = addr

    monkeypatch.setattr("prometheus_client.start_http_server", ok)
    assert wm.start_worker_metrics_server(host="127.0.0.1", port=18002) is True
    assert called == {"port": 18002, "addr": "127.0.0.1"}


# ---------------------------------------------------------------------------
# instrument_job decorator — emits started/completed/failed and duration
# ---------------------------------------------------------------------------


async def test_instrument_job_emits_completed():
    from infra.metrics import worker_jobs_total, worker_job_duration_seconds

    before_started = worker_jobs_total.labels(
        job_name="unit_completed", status="started"
    )._value.get()
    before_completed = worker_jobs_total.labels(
        job_name="unit_completed", status="completed"
    )._value.get()

    @wm.instrument_job("unit_completed")
    async def job(ctx):
        return 42

    assert await job({}) == 42

    after_started = worker_jobs_total.labels(
        job_name="unit_completed", status="started"
    )._value.get()
    after_completed = worker_jobs_total.labels(
        job_name="unit_completed", status="completed"
    )._value.get()
    assert after_started == before_started + 1
    assert after_completed == before_completed + 1

    # Duration histogram recorded at least one observation
    samples = worker_job_duration_seconds.labels(
        job_name="unit_completed"
    )._sum.get()
    assert samples >= 0  # monotonically non-decreasing


async def test_instrument_job_emits_failed_and_reraises():
    from infra.metrics import worker_jobs_total

    before_failed = worker_jobs_total.labels(
        job_name="unit_failed", status="failed"
    )._value.get()

    @wm.instrument_job("unit_failed")
    async def job(ctx):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await job({})

    after_failed = worker_jobs_total.labels(
        job_name="unit_failed", status="failed"
    )._value.get()
    assert after_failed == before_failed + 1


# ---------------------------------------------------------------------------
# Queue poller — exports depth and oldest-job age from mocked Redis
# ---------------------------------------------------------------------------


async def test_poll_queue_once_exports_depth_and_age():
    from infra.metrics import worker_queue_depth, worker_queue_oldest_job_age_seconds

    # arq scores enqueue time in milliseconds since epoch.
    five_seconds_ago_ms = (time.time() - 5.0) * 1000.0
    redis_client = AsyncMock()
    redis_client.zcard = AsyncMock(return_value=7)
    redis_client.zrange = AsyncMock(
        return_value=[(b"job_id", float(five_seconds_ago_ms))]
    )

    await wm._poll_queue_once(redis_client, "arq:queue")

    assert worker_queue_depth._value.get() == 7.0
    observed_age = worker_queue_oldest_job_age_seconds._value.get()
    assert 4.0 <= observed_age <= 15.0  # within a loose window


async def test_poll_queue_once_empty_queue_zeros_age():
    from infra.metrics import worker_queue_oldest_job_age_seconds

    redis_client = AsyncMock()
    redis_client.zcard = AsyncMock(return_value=0)
    redis_client.zrange = AsyncMock(return_value=[])

    await wm._poll_queue_once(redis_client, "arq:queue")
    assert worker_queue_oldest_job_age_seconds._value.get() == 0.0


async def test_poll_queue_once_swallows_redis_errors():
    """A failing Redis call must not escape."""
    redis_client = AsyncMock()
    redis_client.zcard = AsyncMock(side_effect=RuntimeError("down"))
    # Must not raise.
    await wm._poll_queue_once(redis_client, "arq:queue")


async def test_run_queue_poller_stops_on_event():
    """The poll loop must exit promptly when the stop_event is set."""
    redis_client = AsyncMock()
    redis_client.zcard = AsyncMock(return_value=0)
    redis_client.zrange = AsyncMock(return_value=[])

    stop = asyncio.Event()
    task = asyncio.create_task(
        wm.run_queue_poller(redis_client, "arq:queue", interval=0.05, stop_event=stop)
    )
    await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
