"""Spike / burst-load resilience tests.

These tests validate the mitigations added for the burst-load audit:

    #1  single-flight COUNT(*)                       → test_count_is_single_flighted
    #2  TMDb 429 jitter                              → test_tmdb_429_retry_is_jittered
    #3  TMDb transport-error jitter                  → test_tmdb_transport_retry_is_jittered
    #6  enrichment backlog cap + high-watermark      → test_enrichment_backlog_drops_past_cap
    #7  loud warning on rate-limit fallback          → test_rate_limit_memory_fallback_warns_once
    #8  CIDR-aware trusted proxy matching            → test_trusted_proxy_cidr_matching
    #9  /ready response caching                      → test_ready_endpoint_is_cached

These are unit-level spike tests — they mock the expensive boundary
(MySQL / TMDb / Redis) and assert that the mitigation's contract holds
under concurrent pressure. For end-to-end load validation, drive the
running app with a Locust suite using the same invariants.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from infra import rate_limit
from movies.projection_enrichment import ProjectionEnrichmentCoordinator
from movies.query_builder import ImdbRandomMovieFetcher
from movies.tmdb_client import TMDbHelper

pytestmark = pytest.mark.spike


# ---------------------------------------------------------------------------
# #1 Single-flight COUNT(*)
# ---------------------------------------------------------------------------


class _InMemoryCache:
    """Tiny cache matching the SimpleCacheManager interface used here.

    The single-flight relies on the lock-holder populating the cache so
    queued callers return from the cache re-read — so the test needs a
    real cache, not None.
    """

    def __init__(self) -> None:
        self._data: dict[tuple, object] = {}
        self._locks: set[tuple] = set()

    async def get(self, namespace, key):
        return self._data.get((namespace, key))

    async def set(self, namespace, key, value, ttl=None):
        self._data[(namespace, key)] = value

    async def try_acquire_lock(self, namespace, key, ttl_seconds):
        lock_key = (namespace, key)
        if lock_key in self._locks:
            return False
        self._locks.add(lock_key)
        return True

    async def release_lock(self, namespace, key):
        self._locks.discard((namespace, key))


async def test_count_is_single_flighted():
    """100 concurrent callers on a cold cache → exactly ONE DB COUNT query.

    Validates the per-key asyncio.Lock in
    ``ImdbRandomMovieFetcher._count_qualifying_rows``. Without the lock,
    a cache-generation bump followed by a burst of /next_movie calls
    would fire N concurrent full-table COUNTs.
    """
    call_count = 0
    count_started = asyncio.Event()
    release_count = asyncio.Event()

    class SlowPool:
        async def execute(self, query, params=None, fetch=None):
            nonlocal call_count
            call_count += 1
            count_started.set()
            # Hold the "query" open so all 100 callers pile up on the lock.
            await release_count.wait()
            return {"c": 42}

    fetcher = ImdbRandomMovieFetcher(SlowPool(), cache=_InMemoryCache())
    criteria = {"min_year": 1990, "max_year": 2025, "min_votes": 50000}

    async def one_caller():
        return await fetcher._count_qualifying_rows(
            criteria, parameters=[], use_cache=True, use_recent=False, lang="en"
        )

    tasks = [asyncio.create_task(one_caller()) for _ in range(100)]

    # Wait until the first query is in flight, then release it.
    await asyncio.wait_for(count_started.wait(), timeout=1.0)
    release_count.set()
    results = await asyncio.gather(*tasks)

    assert call_count == 1, (
        f"Expected single-flight to collapse 100 callers → 1 DB call, got {call_count}"
    )
    assert all(r == 42 for r in results)


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

    # First response: 429 with Retry-After=2. Second: success.
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

    # Reset the shared circuit breaker so other tests don't leak state.
    helper._circuit_breaker._failure_count = 0
    helper._circuit_breaker._state = helper._circuit_breaker.CLOSED

    await asyncio.gather(
        helper._get("movie/1", metric_endpoint="test"),
        helper._get("movie/2", metric_endpoint="test"),
    )

    # Both retries should have a sleep. They must NOT be equal — jitter
    # makes that astronomically unlikely.
    retry_sleeps = [d for d in sleep_durations if d >= 2.0]
    assert len(retry_sleeps) >= 2, f"Expected ≥2 retry sleeps, got {sleep_durations}"
    assert retry_sleeps[0] != retry_sleeps[1], (
        f"Retry sleeps are identical ({retry_sleeps[0]}s) — jitter is missing"
    )
    # Jitter is bounded at Retry-After + 1s.
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

    # Expect 2 retry sleeps, each in the jittered band [1.0, 2.0).
    retry_sleeps = [d for d in sleep_durations if 1.0 <= d < 2.0]
    assert len(retry_sleeps) == 2, f"Expected 2 retry sleeps in [1,2), got {sleep_durations}"
    assert retry_sleeps[0] != retry_sleeps[1], "Transport-error backoffs are identical — jitter missing"


# ---------------------------------------------------------------------------
# #6 Enrichment backlog cap + high-watermark
# ---------------------------------------------------------------------------


async def test_enrichment_backlog_drops_past_cap(caplog):
    """Backlog beyond cap: schedules return False and a warning is emitted."""
    caplog.set_level(logging.WARNING)

    store = MagicMock()
    store._mark_attempt = AsyncMock()
    coordinator = ProjectionEnrichmentCoordinator(
        store,
        tmdb_helper=MagicMock(),
        enqueue_fn=None,
        local_concurrency=1,
        max_pending=3,
    )

    # Monkey-patch the runner so tasks never complete; we only test the
    # schedule decision, not the enrichment work itself.
    async def never_finish(*args, **kwargs):
        await asyncio.Event().wait()

    coordinator.enrich_projection = never_finish  # type: ignore[assignment]

    # Fill the cap. Each call adds the tconst to the pending set via
    # _schedule_local_enrichment, which is what maybe_enqueue falls back to.
    results = []
    for i in range(5):
        results.append(await coordinator._schedule_local_enrichment(f"tt{i:07d}"))

    # First 3 should schedule; the 4th+ must be dropped.
    assert results[:3] == [True, True, True]
    assert results[3:] == [False, False]
    assert any("backlog full" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# #7 Rate-limit in-memory fallback warns once
# ---------------------------------------------------------------------------


async def test_rate_limit_memory_fallback_warns_once(caplog, monkeypatch):
    """The loud error log fires exactly once across many fallback calls."""
    caplog.set_level(logging.ERROR, logger="infra.rate_limit")

    # Reset module state.
    monkeypatch.setattr(rate_limit, "_memory_fallback_warned", False)
    rate_limit._rate_limit_store.clear()

    # Stub out current_app so SESSION_REDIS is None → triggers memory path.
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
        f"Expected exactly 1 degraded-warning across 10 calls, got {len(degraded_errors)}"
    )


# ---------------------------------------------------------------------------
# #8 CIDR-aware trusted proxy matching
# ---------------------------------------------------------------------------


def test_trusted_proxy_cidr_matching(monkeypatch):
    """IPs inside a configured CIDR are trusted; IPs outside are not."""
    from infra import client_ip

    # Bust the lru_cache between tests.
    client_ip._cached_trusted_networks.cache_clear()
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8, 192.168.1.5")

    assert client_ip._is_trusted("10.5.5.5") is True  # inside /8
    assert client_ip._is_trusted("10.0.0.1") is True
    assert client_ip._is_trusted("192.168.1.5") is True  # bare IP
    assert client_ip._is_trusted("192.168.1.6") is False  # adjacent, not listed
    assert client_ip._is_trusted("8.8.8.8") is False
    assert client_ip._is_trusted("") is False
    assert client_ip._is_trusted("not-an-ip") is False


def test_trusted_proxy_invalid_entry_is_skipped(monkeypatch, caplog):
    """Malformed TRUSTED_PROXIES entries log a warning and are ignored."""
    from infra import client_ip

    client_ip._cached_trusted_networks.cache_clear()
    caplog.set_level(logging.WARNING, logger="infra.client_ip")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8,garbage,,192.168.1.1")

    networks = client_ip.trusted_networks()
    # Two valid entries kept, "garbage" dropped with a warning.
    assert len(networks) == 2
    assert any("invalid entry" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# #9 /ready endpoint caching
# ---------------------------------------------------------------------------


async def test_ready_endpoint_is_cached(monkeypatch):
    """Burst of /ready callers → _compute_readiness runs ONCE within TTL."""
    import routes

    # Clear any previous cached state.
    routes._ready_cache_entry = None

    call_count = 0

    async def fake_compute(_mm):
        nonlocal call_count
        call_count += 1
        return {"status": "ready"}, 200

    monkeypatch.setattr(routes, "_compute_readiness", fake_compute)
    monkeypatch.setattr(routes, "check_ops_auth", lambda: True)

    async def always_ok(_key):
        return True

    monkeypatch.setattr(routes, "check_rate_limit", always_ok)

    fake_services = MagicMock()
    fake_services.movie_manager = MagicMock()
    monkeypatch.setattr(routes, "_services", lambda: fake_services)

    # 50 concurrent "requests" funnel through the cached + single-flighted path.
    view = routes.readiness_check
    results = await asyncio.gather(*[view() for _ in range(50)])

    assert call_count == 1, (
        f"Expected cache to collapse 50 /ready calls → 1 compute, got {call_count}"
    )
    assert all(r[1] == 200 for r in results)

    # Expire the cache and the next call should recompute exactly once.
    routes._ready_cache_entry = None
    await view()
    assert call_count == 2
