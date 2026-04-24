"""Spike / burst-load resilience tests — contracts that aren't covered elsewhere.

This file originally covered 8 burst-load mitigations. Three of them are now
covered by targeted unit tests (#6 enrichment backlog cap in
``tests/movies/test_projection_enrichment.py``, #8 trusted-proxy basics in
``tests/infra/test_client_ip.py``) and one has been removed with its
subsystem (#1 single-flight COUNT died with ``MovieCountCache``). The
remaining four contracts still matter in production but have no alternative
coverage, so they live here:

    #2  TMDb 429 retry is jittered          → test_tmdb_429_retry_is_jittered
    #3  TMDb transport-error retry jitter   → test_tmdb_transport_retry_is_jittered
    #7  rate-limit memory fallback warns    → test_rate_limit_memory_fallback_warns_once
    #9  /ready response is cached           → test_ready_endpoint_is_cached
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import httpx
import pytest

from infra import rate_limit
from movies.tmdb_client import TMDbHelper

pytestmark = pytest.mark.spike


# ---------------------------------------------------------------------------
# #2 TMDb 429 retry jitter
# ---------------------------------------------------------------------------


async def test_tmdb_429_retry_is_jittered(monkeypatch):
    """Two concurrent 429-retrying callers must NOT sleep identical durations.

    Without jitter, both would sleep exactly ``Retry-After`` seconds and
    wake in lockstep, re-tripping the circuit breaker.
    """
    sleep_durations: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleep_durations.append(d)
        await real_sleep(0)  # yield, don't actually wait

    monkeypatch.setattr("movies.tmdb_client.asyncio.sleep", fake_sleep)

    helper = TMDbHelper("1234567890abcdef1234567890abcdef")
    helper._max_retries = 1

    call_n = {"n": 0}

    async def fake_get(url, params=None, headers=None):
        call_n["n"] += 1
        if call_n["n"] <= 2:  # each caller's first attempt 429s
            r = MagicMock()
            r.status_code = 429
            r.headers = {"Retry-After": "2"}
            r.raise_for_status = MagicMock()
            return r
        r = MagicMock()
        r.status_code = 200
        r.headers = {}
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value={"ok": True})
        return r

    helper._client.get = fake_get  # type: ignore[method-assign]
    helper._circuit_breaker._failure_count = 0
    helper._circuit_breaker._state = helper._circuit_breaker.CLOSED

    await asyncio.gather(
        helper._get("movie/1", metric_endpoint="test"),
        helper._get("movie/2", metric_endpoint="test"),
    )

    retry_sleeps = [d for d in sleep_durations if d >= 2.0]
    assert len(retry_sleeps) >= 2, f"Expected ≥2 retry sleeps, got {sleep_durations}"
    assert retry_sleeps[0] != retry_sleeps[1], (
        f"Retry sleeps are identical ({retry_sleeps[0]}s) — jitter is missing"
    )
    for d in retry_sleeps:
        assert 2.0 <= d <= 3.0, f"Retry sleep {d}s outside expected jitter band"


# ---------------------------------------------------------------------------
# #3 TMDb transport-error retry jitter
# ---------------------------------------------------------------------------


async def test_tmdb_transport_retry_is_jittered(monkeypatch):
    """Two concurrent callers hitting transport errors don't sleep in lockstep."""
    sleep_durations: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleep_durations.append(d)
        await real_sleep(0)

    monkeypatch.setattr("movies.tmdb_client.asyncio.sleep", fake_sleep)

    helper = TMDbHelper("1234567890abcdef1234567890abcdef")
    helper._max_retries = 1

    state = {"n": 0}

    async def flaky_get(url, params=None, headers=None):
        state["n"] += 1
        if state["n"] <= 2:
            raise httpx.RequestError("boom")
        r = MagicMock()
        r.status_code = 200
        r.headers = {}
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value={"ok": True})
        return r

    helper._client.get = flaky_get  # type: ignore[method-assign]
    helper._circuit_breaker._failure_count = 0
    helper._circuit_breaker._state = helper._circuit_breaker.CLOSED

    await asyncio.gather(
        helper._get("movie/1", metric_endpoint="test"),
        helper._get("movie/2", metric_endpoint="test"),
    )

    retry_sleeps = [d for d in sleep_durations if 1.0 <= d < 2.0]
    assert len(retry_sleeps) == 2, (
        f"Expected 2 retry sleeps in [1,2), got {sleep_durations}"
    )
    assert retry_sleeps[0] != retry_sleeps[1], (
        "Transport-error backoffs are identical — jitter missing"
    )


# ---------------------------------------------------------------------------
# #7 Rate-limit in-memory fallback warns once
# ---------------------------------------------------------------------------


async def test_rate_limit_memory_fallback_warns_once(caplog, monkeypatch):
    """The loud error log fires exactly once across many fallback calls."""
    caplog.set_level(logging.ERROR, logger="infra.rate_limit")

    monkeypatch.setattr(rate_limit, "_memory_fallback_warned", False)
    rate_limit._rate_limit_store.clear()

    fake_app = MagicMock()
    fake_app.config = {"SESSION_REDIS": None}
    monkeypatch.setattr(rate_limit, "current_app", fake_app)
    monkeypatch.setattr(rate_limit, "get_client_ip", lambda: "127.0.0.1")

    for _ in range(10):
        await rate_limit.check_rate_limit("test_endpoint")

    degraded_errors = [
        rec for rec in caplog.records if "RATE LIMITER DEGRADED" in rec.message
    ]
    assert len(degraded_errors) == 1, (
        f"Expected exactly 1 degraded-warning across 10 calls, "
        f"got {len(degraded_errors)}"
    )


# ---------------------------------------------------------------------------
# #9 /ready response caching
# ---------------------------------------------------------------------------


async def test_ready_endpoint_is_cached(monkeypatch):
    """Burst of /ready callers → _compute_readiness runs ONCE within TTL."""
    from nextreel.web.routes import ops as routes_ops

    routes_ops._ready_cache_entry = None

    call_count = 0

    async def fake_compute(_mm):
        nonlocal call_count
        call_count += 1
        return {"status": "ready"}, 200

    monkeypatch.setattr(routes_ops, "_compute_readiness", fake_compute)
    monkeypatch.setattr(routes_ops, "check_ops_auth", lambda: True)

    async def always_ok(_key):
        return True

    monkeypatch.setattr(routes_ops, "check_rate_limit", always_ok)

    fake_services = MagicMock()
    fake_services.movie_manager = MagicMock()
    monkeypatch.setattr(routes_ops, "_services", lambda: fake_services)

    view = routes_ops.readiness_check
    results = await asyncio.gather(*[view() for _ in range(50)])

    assert call_count == 1, (
        f"Expected cache to collapse 50 /ready calls → 1 compute, got {call_count}"
    )
    assert all(r[1] == 200 for r in results)

    routes_ops._ready_cache_entry = None
    await view()
    assert call_count == 2


