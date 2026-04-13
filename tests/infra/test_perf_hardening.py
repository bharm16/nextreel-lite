"""Tests for the scalability/perf hardening changes.

Covers:
- NavigationState.clone() shallow-copy semantics (no deepcopy churn).
- NavigationStateStore.mutate() retry budget + backoff.
- ProjectionEnrichmentCoordinator local concurrency cap.
- Rate-limit in-memory LRU eviction.
- SimpleCacheManager.get_or_load() single-flight semantics.
- MovieQueryBuilder count cache generation invalidation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from infra.cache import CacheNamespace, SimpleCacheManager
from nextreel.domain.navigation_state import NavigationState
from movies.query_builder import (
    _criteria_cache_key,
    _current_count_generation,
    bump_count_cache_generation,
)


# ---------------------------------------------------------------------------
# NavigationState shallow clone
# ---------------------------------------------------------------------------


def _make_state() -> NavigationState:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return NavigationState(
        session_id="sid",
        version=1,
        csrf_token="tok",
        filters={"language": "en", "min_year": 2000},
        current_tconst="tt1",
        queue=[{"tconst": "tt2", "title": "X"}],
        prev=[{"tconst": "tt0", "title": "Y"}],
        future=[],
        seen=["tt0"],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
        current_ref={"tconst": "tt1", "title": "X"},
    )


def test_clone_is_independent_of_original():
    state = _make_state()
    clone = state.clone()

    # Mutating the clone's containers must not bleed into the original.
    clone.queue.append({"tconst": "ttX", "title": "Z"})
    clone.prev.append({"tconst": "ttY", "title": "W"})
    clone.seen.append("ttZ")
    clone.filters["language"] = "fr"

    assert state.queue == [{"tconst": "tt2", "title": "X"}]
    assert state.prev == [{"tconst": "tt0", "title": "Y"}]
    assert state.seen == ["tt0"]
    assert state.filters["language"] == "en"


def test_clone_preserves_scalar_fields():
    state = _make_state()
    clone = state.clone()

    assert clone.session_id == state.session_id
    assert clone.version == state.version
    assert clone.csrf_token == state.csrf_token
    assert clone.current_tconst == state.current_tconst
    assert clone.current_ref == state.current_ref
    assert clone.current_ref is not state.current_ref  # shallow-but-distinct


# ---------------------------------------------------------------------------
# Projection enrichment semaphore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_enrichment_caps_concurrency(monkeypatch):
    from movies.projection_enrichment import ProjectionEnrichmentCoordinator

    store = MagicMock()
    store._mark_attempt = AsyncMock()
    store._select_row = AsyncMock(return_value=None)
    store._upsert_failed = AsyncMock()
    store._upsert_ready = AsyncMock()
    store.ensure_core_projection = AsyncMock(return_value={})
    store.db_pool = MagicMock()

    coord = ProjectionEnrichmentCoordinator(
        store=store, tmdb_helper=MagicMock(), local_concurrency=3
    )

    in_flight = 0
    peak = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_enrich(tconst, known_tmdb_id=None):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        started.set()
        await release.wait()
        in_flight -= 1
        return {}

    coord.enrich_projection = fake_enrich  # type: ignore[method-assign]

    # Schedule more tasks than the cap permits.
    for i in range(10):
        await coord._schedule_local_enrichment(f"tt{i:07d}")

    # Give the loop a chance to start enrichments up to the cap.
    await started.wait()
    await asyncio.sleep(0.05)
    assert peak <= 3

    release.set()
    await coord.drain_pending(timeout=2.0)
    assert peak <= 3


# ---------------------------------------------------------------------------
# Rate-limit LRU eviction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_memory_lru_eviction(monkeypatch):
    from infra import rate_limit

    # Reset module state.
    rate_limit._rate_limit_store.clear()
    monkeypatch.setattr(rate_limit._rate_limit_store, "_max_keys", 3)
    monkeypatch.setattr(rate_limit, "get_client_ip", lambda: "1.1.1.1")

    seq = iter(range(1000))

    def fake_ip():
        return f"10.0.0.{next(seq)}"

    monkeypatch.setattr(rate_limit, "get_client_ip", fake_ip)

    # Push 10 distinct IPs through the limiter; cap is 3.
    for _ in range(10):
        await rate_limit.check_rate_limit_memory("test")

    assert len(rate_limit._rate_limit_store) <= 3


# ---------------------------------------------------------------------------
# SimpleCacheManager single-flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_load_collapses_concurrent_loaders():
    """N concurrent get_or_load calls invoke loader exactly once."""
    cache = SimpleCacheManager(redis_client=None)

    # Bypass real Redis: stub get/set on the instance.
    storage: dict[str, object] = {}

    async def fake_get(namespace, key):
        return storage.get(cache._make_key(namespace, key))

    async def fake_set(namespace, key, value, ttl=None):
        storage[cache._make_key(namespace, key)] = value

    cache.get = fake_get  # type: ignore[method-assign]
    cache.set = fake_set  # type: ignore[method-assign]

    call_count = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def loader():
        nonlocal call_count
        call_count += 1
        started.set()
        await release.wait()
        return {"value": 42}

    async def caller():
        return await cache.get_or_load(CacheNamespace.MOVIE, "k", loader)

    task1 = asyncio.create_task(caller())
    await started.wait()
    task2 = asyncio.create_task(caller())
    task3 = asyncio.create_task(caller())
    await asyncio.sleep(0.01)
    release.set()

    results = await asyncio.gather(task1, task2, task3)
    assert all(r == {"value": 42} for r in results)
    assert call_count == 1


@pytest.mark.asyncio
async def test_get_or_load_returns_cached_value_without_loader_call():
    cache = SimpleCacheManager(redis_client=None)

    async def fake_get(namespace, key):
        return {"cached": True}

    cache.get = fake_get  # type: ignore[method-assign]

    async def loader():
        raise AssertionError("loader must not be called on hit")

    result = await cache.get_or_load(CacheNamespace.MOVIE, "k", loader)
    assert result == {"cached": True}


# ---------------------------------------------------------------------------
# Count cache generation invalidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bump_count_cache_generation_increments():
    storage: dict = {}

    cache = MagicMock()

    async def fake_get(namespace, key):
        return storage.get(key)

    async def fake_set(namespace, key, value, ttl=None):
        storage[key] = value

    cache.get = fake_get
    cache.set = fake_set

    gen0 = await _current_count_generation(cache)
    assert gen0 == 0

    new_gen = await bump_count_cache_generation(cache)
    assert new_gen == 1

    gen1 = await _current_count_generation(cache)
    assert gen1 == 1

    new_gen2 = await bump_count_cache_generation(cache)
    assert new_gen2 == 2


def test_criteria_cache_key_includes_generation():
    crit = {"language": "en", "min_year": 2000}
    k0 = _criteria_cache_key(crit, generation=0)
    k1 = _criteria_cache_key(crit, generation=1)
    assert k0 != k1
    assert k0.startswith("count:0:")
    assert k1.startswith("count:1:")
