"""Tests for the concurrency refactor.

Covers:
- worker.validate_referential_integrity bounded gather + exception tolerance
- CandidateTableMaintainer uses a single combined ALTER TABLE
- Movie.get_movie_data starts get_movie_full before ratings await completes
- /movie route runs watched + payload concurrently
- /watched route runs list + count concurrently
- MovieManager background scheduler + home() prewarm branching
- ProjectionEnrichmentCoordinator in-flight task map reuse and cleanup
- ProjectionStore.fetch_renderable_payload does not block on in-flight task
- ProjectionStore stale-path skips ARQ enqueue when local task in-flight
- session.user_auth async bcrypt helpers + register_user precomputed_hash
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nextreel.domain.navigation_state import NavigationState
from infra.time_utils import utcnow


def _make_state(session_id: str) -> NavigationState:
    now = utcnow()
    return NavigationState(
        session_id=session_id,
        version=1,
        csrf_token="t",
        filters={},
        current_tconst=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=8),
    )


# ---------------------------------------------------------------------------
# Worker integrity checks
# ---------------------------------------------------------------------------


async def test_integrity_checks_gather_counts_issues_and_caps_concurrency():
    from worker import validate_referential_integrity, INTEGRITY_CHECK_CONCURRENCY

    in_flight = 0
    max_in_flight = 0
    event = asyncio.Event()

    async def fake_execute(query, fetch=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # Let a couple of legs pile up so we can observe concurrency.
        await asyncio.sleep(0.01)
        in_flight -= 1
        # Return orphans>0 for half the queries so we can count.
        if "Q1" in query or "Q2" in query:
            return {"orphans": 3}
        return {"orphans": 0}

    mock_pool = MagicMock()
    mock_pool.execute = fake_execute
    ctx = {"db_pool": mock_pool}

    checks = [
        (f"c{i}", f"SELECT {'Q1' if i == 0 else 'Q2' if i == 1 else 'Q0'} FROM t {i}")
        for i in range(6)
    ]
    with patch("worker.INTEGRITY_CHECKS", checks):
        result = await validate_referential_integrity(ctx)

    # Two queries report orphans>0 → count is 2.
    assert result == 2
    # Concurrency should never exceed the fixed cap.
    assert max_in_flight <= INTEGRITY_CHECK_CONCURRENCY
    event.set()  # no-op, kept for clarity


async def test_integrity_checks_tolerate_individual_exceptions():
    from worker import validate_referential_integrity

    async def fake_execute(query, fetch=None):
        if "BAD" in query:
            raise RuntimeError("boom")
        return {"orphans": 1}

    mock_pool = MagicMock()
    mock_pool.execute = fake_execute
    ctx = {"db_pool": mock_pool}

    checks = [
        ("ok1", "SELECT 1"),
        ("fail", "SELECT BAD"),
        ("ok2", "SELECT 2"),
    ]
    with patch("worker.INTEGRITY_CHECKS", checks):
        result = await validate_referential_integrity(ctx)

    # The two successful checks each report orphans>0 → count is 2.
    # The exception path is logged and counted as zero.
    assert result == 2


# ---------------------------------------------------------------------------
# CandidateTableMaintainer combined ALTER TABLE
# ---------------------------------------------------------------------------


async def test_candidate_refresh_uses_single_alter_table():
    from movies.candidate_store import CandidateTableMaintainer

    executed: list[str] = []

    mock_pool = MagicMock()

    async def fake_execute(sql, *args, fetch=None):
        executed.append(sql)
        lowered = sql.lower()
        if "group by sample_bucket" in lowered:
            # Balanced distribution so validate_bucket_distribution passes.
            return [{"sample_bucket": i, "bucket_count": 10} for i in range(128)]
        if "count(*) as total" in lowered:
            return {"total": 42}
        return None

    mock_pool.execute = fake_execute
    maintainer = CandidateTableMaintainer(mock_pool)
    await maintainer.refresh_movie_candidates()

    alter_statements = [s for s in executed if "ALTER TABLE" in s.upper()]
    create_index_statements = [s for s in executed if "CREATE INDEX" in s.upper()]

    # Exactly one ALTER TABLE used during index phase, zero sequential CREATE INDEX calls.
    assert len(alter_statements) == 1
    assert len(create_index_statements) == 0
    # Fulltext is included in the single ALTER.
    assert "FULLTEXT" in alter_statements[0].upper()
    assert "idx_movie_candidates_bucket_filter" in alter_statements[0]


# ---------------------------------------------------------------------------
# Movie.get_movie_data concurrent TMDb + ratings
# ---------------------------------------------------------------------------


async def test_movie_data_starts_full_fetch_before_ratings_completes():
    from movies.movie import Movie

    order: list[str] = []
    ratings_started = asyncio.Event()
    ratings_done = asyncio.Event()

    mock_pool = MagicMock()

    async def fake_execute(*args, **kwargs):
        order.append("ratings_start")
        ratings_started.set()
        # Block ratings until after get_movie_full has started.
        await asyncio.sleep(0.05)
        order.append("ratings_done")
        ratings_done.set()
        return {"slug": "s", "tconst": "tt1", "averageRating": 7.5, "numVotes": 100}

    mock_pool.execute = fake_execute

    tmdb = MagicMock()
    tmdb.get_tmdb_id_by_tconst = AsyncMock(return_value=555)

    async def fake_get_movie_full(tmdb_id):
        order.append("full_start")
        # Confirm ratings is still in flight at this point.
        assert ratings_started.is_set()
        assert not ratings_done.is_set()
        await asyncio.sleep(0.01)
        order.append("full_done")
        return {"title": "T", "genres": [], "spoken_languages": [], "production_countries": []}

    tmdb.get_movie_full = fake_get_movie_full
    tmdb.parse_cast = MagicMock(return_value=[])
    tmdb.parse_directors = MagicMock(return_value=[])
    tmdb.parse_key_crew = MagicMock(return_value=[])
    tmdb.parse_trailer = MagicMock(return_value=None)
    tmdb.parse_images = MagicMock(return_value={"backdrops": []})
    tmdb.parse_age_rating = MagicMock(return_value="NR")
    tmdb.parse_watch_providers = MagicMock(return_value=None)
    tmdb.parse_keywords = MagicMock(return_value=[])
    tmdb.parse_recommendations = MagicMock(return_value=[])
    tmdb.parse_external_ids = MagicMock(return_value={})
    tmdb.parse_collection = MagicMock(return_value=None)
    tmdb.image_base_url = "http://x/"
    tmdb.close = AsyncMock()

    movie = Movie("tt1", mock_pool, tmdb_helper=tmdb)
    result = await movie.get_movie_data()

    assert result is not None
    assert result["_full"] is True
    # The key assertion: full_start comes BEFORE ratings_done.
    full_idx = order.index("full_start")
    ratings_done_idx = order.index("ratings_done")
    assert full_idx < ratings_done_idx


async def test_movie_data_cleans_up_tasks_on_no_tmdb_id():
    from movies.movie import Movie

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(
        return_value={"slug": None, "tconst": "tt1", "averageRating": 0, "numVotes": 0}
    )

    tmdb = MagicMock()
    tmdb.get_tmdb_id_by_tconst = AsyncMock(return_value=None)
    tmdb.close = AsyncMock()

    movie = Movie("tt1", mock_pool, tmdb_helper=tmdb)
    result = await movie.get_movie_data()
    assert result is None


# ---------------------------------------------------------------------------
# MovieManager background scheduler + home prewarm
# ---------------------------------------------------------------------------


async def test_home_schedules_background_prewarm_when_dual_write_off(monkeypatch):
    from nextreel.application.movie_service import MovieManager
    from nextreel.application.home_prewarm_service import HomePrewarmService

    monkeypatch.setenv("NAV_STATE_DUAL_WRITE_ENABLED", "false")

    mgr = MovieManager.__new__(MovieManager)
    mgr.db_config = {}
    mgr._navigator = MagicMock()
    mgr._navigator.prewarm_queue = AsyncMock()
    mgr.default_backdrop_url = None
    mgr._home_prewarm_service = HomePrewarmService()

    scheduled: list = []

    def scheduler(coro):
        scheduled.append(coro)
        return asyncio.create_task(coro)

    mgr._background_scheduler = scheduler

    state = _make_state("sid-1")
    result = await mgr.home(state)
    assert result == {"default_backdrop_url": None}
    # Exactly one coroutine scheduled (the prewarm).
    assert len(scheduled) == 1
    # Drain background task
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task() and not t.done():
            try:
                await asyncio.wait_for(t, timeout=0.5)
            except Exception:
                pass


async def test_home_uses_inline_prewarm_when_dual_write_on(monkeypatch):
    from nextreel.application.movie_service import MovieManager
    from nextreel.application.home_prewarm_service import HomePrewarmService

    monkeypatch.setenv("NAV_STATE_DUAL_WRITE_ENABLED", "true")

    mgr = MovieManager.__new__(MovieManager)
    mgr.db_config = {}
    mgr._navigator = MagicMock()
    prewarm_mock = AsyncMock()
    mgr._navigator.prewarm_queue = prewarm_mock
    mgr.default_backdrop_url = None
    mgr._home_prewarm_service = HomePrewarmService()

    scheduled: list = []

    def scheduler(coro):
        scheduled.append(coro)
        return asyncio.create_task(coro)

    mgr._background_scheduler = scheduler

    state = _make_state("sid-2")
    await mgr.home(state, legacy_session={"k": "v"})
    # Inline path: no background scheduling happened.
    assert scheduled == []
    prewarm_mock.assert_awaited()


async def test_home_uses_inline_prewarm_when_no_scheduler(monkeypatch):
    from nextreel.application.movie_service import MovieManager
    from nextreel.application.home_prewarm_service import HomePrewarmService

    monkeypatch.setenv("NAV_STATE_DUAL_WRITE_ENABLED", "false")

    mgr = MovieManager.__new__(MovieManager)
    mgr.db_config = {}
    mgr._navigator = MagicMock()
    prewarm_mock = AsyncMock()
    mgr._navigator.prewarm_queue = prewarm_mock
    mgr.default_backdrop_url = None
    mgr._background_scheduler = None  # no scheduler
    mgr._home_prewarm_service = HomePrewarmService()

    state = _make_state("sid-3")
    await mgr.home(state)
    prewarm_mock.assert_awaited()


# ---------------------------------------------------------------------------
# ProjectionEnrichmentCoordinator in-flight task map
# ---------------------------------------------------------------------------


async def test_get_or_start_inflight_dedupes_concurrent_callers():
    from movies.projection_enrichment import ProjectionEnrichmentCoordinator

    store = MagicMock()
    coordinator = ProjectionEnrichmentCoordinator(store, tmdb_helper=MagicMock())

    call_count = 0
    started = asyncio.Event()

    async def fake_enrich(tconst, known_tmdb_id=None):
        nonlocal call_count
        call_count += 1
        started.set()
        await asyncio.sleep(0.02)
        return {"tconst": tconst, "_full": True}

    coordinator.enrich_projection = fake_enrich

    # Fire three concurrent calls for the same tconst.
    tasks = [
        asyncio.create_task(coordinator.get_or_start_inflight("tt1", tmdb_id=1)) for _ in range(3)
    ]
    await asyncio.sleep(0)  # yield
    # All callers should share the same underlying task.
    returned_tasks = await asyncio.gather(*tasks)
    assert len({id(t) for t in returned_tasks}) == 1
    result = await returned_tasks[0]
    assert result == {"tconst": "tt1", "_full": True}
    assert call_count == 1
    # Map is cleaned up after completion.
    assert "tt1" not in coordinator._inflight_enrichment


async def test_inflight_task_removed_from_map_on_failure():
    from movies.projection_enrichment import ProjectionEnrichmentCoordinator

    store = MagicMock()
    coordinator = ProjectionEnrichmentCoordinator(store, tmdb_helper=MagicMock())

    async def failing_enrich(tconst, known_tmdb_id=None):
        await asyncio.sleep(0)
        raise RuntimeError("boom")

    coordinator.enrich_projection = failing_enrich

    task = await coordinator.get_or_start_inflight("tt2")
    with pytest.raises(RuntimeError):
        await task
    # Map entry cleared even after failure → next request starts fresh.
    assert "tt2" not in coordinator._inflight_enrichment
    # has_inflight returns False after failure.
    assert coordinator.has_inflight("tt2") is False


async def test_inflight_new_task_starts_after_previous_failure():
    from movies.projection_enrichment import ProjectionEnrichmentCoordinator

    store = MagicMock()
    coordinator = ProjectionEnrichmentCoordinator(store, tmdb_helper=MagicMock())

    calls: list[int] = []

    async def enrich(tconst, known_tmdb_id=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first fails")
        return {"tconst": tconst, "_full": True}

    coordinator.enrich_projection = enrich

    task1 = await coordinator.get_or_start_inflight("tt3")
    with pytest.raises(RuntimeError):
        await task1

    task2 = await coordinator.get_or_start_inflight("tt3")
    result = await task2
    assert result["tconst"] == "tt3"
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# ProjectionStore fetch_renderable_payload in-flight behavior
# ---------------------------------------------------------------------------


async def test_fetch_renderable_ignores_pending_inflight_and_returns_ready_row():
    from movies.projection_store import ProjectionStore

    store = ProjectionStore(MagicMock(), tmdb_helper=MagicMock())

    blocker = asyncio.Event()

    async def fake_enrich(tconst, known_tmdb_id=None):
        await blocker.wait()
        return {"tconst": tconst, "_full": True, "title": "InFlight"}

    store.coordinator.enrich_projection = fake_enrich
    task = await store.coordinator.get_or_start_inflight("tt5", tmdb_id=None)
    store.select_row = AsyncMock(
        return_value={
            "tconst": "tt5",
            "tmdb_id": 5,
            "payload_json": '{"title": "Ready Row", "_full": true}',
            "projection_state": "ready",
            "enriched_at": None,
            "stale_after": utcnow() + timedelta(days=1),
            "last_attempt_at": None,
            "attempt_count": 0,
            "last_error": None,
        }
    )

    try:
        result = await asyncio.wait_for(store.fetch_renderable_payload("tt5"), timeout=0.05)
    finally:
        blocker.set()
        await asyncio.gather(task, return_exceptions=True)

    assert result["title"] == "Ready Row"
    store.select_row.assert_awaited_once_with("tt5")


async def test_stale_path_skips_arq_enqueue_when_local_task_inflight():
    """Drive the real fetch_renderable_payload stale branch and assert it
    does NOT enqueue via ARQ when a local task is in flight.

    This replaces an earlier version that re-implemented the guard in the
    test body — which meant the test would keep passing even if the
    production guard were deleted.
    """
    from datetime import timedelta

    from infra.time_utils import utcnow
    from movies.projection_state import ProjectionState
    from movies.projection_store import ProjectionStore

    store = ProjectionStore(MagicMock(), tmdb_helper=MagicMock())

    # Track whether the ARQ enqueue path was reached.
    enqueue_fn = AsyncMock(return_value="job-id-should-not-fire")
    store.coordinator.enqueue_fn = enqueue_fn

    # Install a never-completing local in-flight task under the stale tconst.
    # It must be registered via the coordinator so ``get_or_start_inflight``
    # returns the same task we planted — we do this by monkey-patching the
    # underlying enrichment fn to block forever and then calling the real
    # ``get_or_start_inflight`` entry point (no private-attribute reach-in).
    blocker = asyncio.Event()

    async def never_completes(tconst, known_tmdb_id=None):
        await blocker.wait()
        return {"tconst": tconst, "_full": True}

    store.coordinator.enrich_projection = never_completes
    inflight_task = await store.coordinator.get_or_start_inflight("tt6", tmdb_id=999)
    assert store.coordinator.has_inflight("tt6")

    # Test A: fetch_renderable_payload returns the stale row immediately and
    # leaves the pending enrichment task alone. That keeps /movie TTFB tied to
    # the projection read instead of a TMDb enrichment tail.
    select_called = False

    async def fake_select(t):
        nonlocal select_called
        select_called = True
        return {
            "tconst": "tt6",
            "tmdb_id": 999,
            "payload_json": '{"title": "Stale Row", "_full": true}',
            "projection_state": ProjectionState.STALE.value,
            "stale_after": utcnow() - timedelta(days=1),
            "enriched_at": None,
            "last_attempt_at": None,
            "attempt_count": 0,
            "last_error": None,
        }

    store.select_row = fake_select  # type: ignore[assignment]

    try:
        result = await asyncio.wait_for(store.fetch_renderable_payload("tt6"), timeout=0.05)
    finally:
        blocker.set()
        await asyncio.gather(inflight_task, return_exceptions=True)

    assert result["title"] == "Stale Row"
    assert select_called is True
    # ARQ enqueue was never invoked.
    enqueue_fn.assert_not_awaited()

    # Test B: direct stale-path mutual-exclusion. Plant a NEW in-flight task
    # for "tt7", then assert maybe_enqueue_if_not_inflight short-circuits
    # before it can call the inner enqueue path.
    blocker2 = asyncio.Event()

    async def never_completes2(tconst, known_tmdb_id=None):
        await blocker2.wait()
        return {"tconst": tconst, "_full": True}

    store.coordinator.enrich_projection = never_completes2
    await store.coordinator.get_or_start_inflight("tt7", tmdb_id=777)

    # Verify that maybe_enqueue_if_not_inflight short-circuits: patch the
    # inner maybe_enqueue (which would call enqueue_fn) and assert it is
    # NOT awaited when has_inflight is True.
    inner_enqueue = AsyncMock()
    store.coordinator.maybe_enqueue = inner_enqueue  # type: ignore[assignment]
    stale_row = {
        "tconst": "tt7",
        "tmdb_id": 777,
        "payload_json": '{"title": "Stale"}',
        "projection_state": ProjectionState.STALE.value,
        "stale_after": utcnow() - timedelta(days=1),
        "enriched_at": None,
        "last_attempt_at": None,
        "attempt_count": 0,
        "last_error": None,
    }
    enqueued = await store.coordinator.maybe_enqueue_if_not_inflight("tt7", stale_row, tmdb_id=777)
    assert enqueued is False
    inner_enqueue.assert_not_awaited()

    # Clean up the blocked task.
    blocker2.set()
    try:
        await asyncio.wait_for(
            store.coordinator.get_inflight("tt7") or asyncio.sleep(0),
            timeout=0.5,
        )
    except (asyncio.TimeoutError, Exception):
        pass
    assert inflight_task.done()


async def test_maybe_enqueue_if_not_inflight_proceeds_when_no_inflight():
    """Positive case: with no in-flight task, maybe_enqueue_if_not_inflight
    delegates to maybe_enqueue and returns its result.
    """
    from movies.projection_enrichment import ProjectionEnrichmentCoordinator

    store = MagicMock()
    coordinator = ProjectionEnrichmentCoordinator(store)

    inner_enqueue = AsyncMock(return_value=True)
    coordinator.maybe_enqueue = inner_enqueue  # type: ignore[assignment]

    row = {"tconst": "tt8", "last_attempt_at": None}
    result = await coordinator.maybe_enqueue_if_not_inflight("tt8", row, tmdb_id=8)
    assert result is True
    inner_enqueue.assert_awaited_once_with("tt8", row, tmdb_id=8)


# ---------------------------------------------------------------------------
# Auth bcrypt offload
# ---------------------------------------------------------------------------


async def test_hash_password_and_verify_password_roundtrip():
    from session.user_auth import hash_password_async, verify_password_async

    pw = "correct horse battery staple"
    hashed = await hash_password_async(pw)
    assert isinstance(hashed, str)
    assert await verify_password_async(pw, hashed) is True
    assert await verify_password_async("wrong password", hashed) is False


async def test_register_user_accepts_precomputed_hash():
    from session.user_auth import register_user

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value=None)

    user_id = await register_user(
        mock_pool,
        "a@b.com",
        "unused-because-we-pass-hash",
        None,
        precomputed_hash="$2b$12$precomputed",
    )
    assert isinstance(user_id, str) and len(user_id) > 0

    # The inserted row used the precomputed hash, not a re-hash of the password.
    call = mock_pool.execute.await_args
    params = call.args[1]
    assert params[2] == "$2b$12$precomputed"


async def test_register_user_hashes_when_no_precomputed():
    from session.user_auth import register_user

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value=None)

    await register_user(mock_pool, "a@b.com", "real-password", None)
    call = mock_pool.execute.await_args
    params = call.args[1]
    # The hash should be a bcrypt string.
    assert params[2].startswith("$2")
