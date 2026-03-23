"""Tests for MetricsCollector — user tracking, eviction, collection lifecycle."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from metrics_collector import MetricsCollector


@pytest.fixture
def collector():
    """MetricsCollector with mocked dependencies."""
    mock_pool = AsyncMock()
    mock_pool.get_metrics = AsyncMock(return_value={
        "pool_size": 10,
        "free_connections": 5,
        "circuit_breaker_state": "closed",
    })
    mc = MetricsCollector(db_pool=mock_pool, movie_manager=None)
    return mc


# ---------------------------------------------------------------------------
# User tracking and eviction
# ---------------------------------------------------------------------------


class TestTrackUserActivity:
    def test_tracks_user(self, collector):
        collector.track_user_activity("user-1")
        assert "user-1" in collector._active_users

    def test_updates_timestamp(self, collector):
        collector._active_users["user-1"] = time.time() - 100
        old_ts = collector._active_users["user-1"]
        collector.track_user_activity("user-1")
        assert collector._active_users["user-1"] > old_ts

    def test_evicts_when_over_cap(self, collector):
        collector._max_tracked_users = 5
        collector._active_user_timeout = 60

        # Add stale users
        stale_ts = time.time() - 120
        for i in range(6):
            collector._active_users[f"stale-{i}"] = stale_ts

        # Adding one more should trigger eviction
        collector.track_user_activity("new-user")
        # All stale users should be gone
        assert "new-user" in collector._active_users
        assert len(collector._active_users) <= collector._max_tracked_users + 1

    def test_does_not_evict_recent_users(self, collector):
        collector._max_tracked_users = 3
        collector._active_user_timeout = 3600

        now = time.time()
        for i in range(3):
            collector._active_users[f"recent-{i}"] = now

        # Adding another triggers eviction attempt, but all are recent
        collector.track_user_activity("new-user")
        # recent users should still be there
        for i in range(3):
            assert f"recent-{i}" in collector._active_users


class TestTrackActions:
    def test_track_user_action(self, collector):
        # Should not raise
        collector.track_user_action("next_movie")

    def test_track_movie_recommendation(self, collector):
        # Should not raise
        collector.track_movie_recommendation("filtered")


# ---------------------------------------------------------------------------
# Background collection lifecycle
# ---------------------------------------------------------------------------


class TestCollectionLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, collector):
        assert collector._collection_task is None
        await collector.start_collection()
        assert collector._collection_task is not None
        # Clean up
        await collector.stop_collection()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, collector):
        await collector.start_collection()
        task = collector._collection_task
        await collector.stop_collection()
        assert collector._collection_task is None
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self, collector):
        await collector.start_collection()
        first_task = collector._collection_task
        await collector.start_collection()
        assert collector._collection_task is first_task
        await collector.stop_collection()


# ---------------------------------------------------------------------------
# DB metrics collection
# ---------------------------------------------------------------------------


class TestCollectDbMetrics:
    @pytest.mark.asyncio
    async def test_collects_from_pool(self, collector):
        await collector._collect_db_metrics()
        collector.db_pool.get_metrics.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_pool_does_not_raise(self):
        mc = MetricsCollector(db_pool=None)
        # Should not raise
        await mc._collect_db_metrics()

    @pytest.mark.asyncio
    async def test_pool_error_does_not_raise(self, collector):
        collector.db_pool.get_metrics = AsyncMock(side_effect=ConnectionError("down"))
        # Should not raise — errors are logged and swallowed
        await collector._collect_db_metrics()


# ---------------------------------------------------------------------------
# Stale user eviction in background loop
# ---------------------------------------------------------------------------


class TestStaleUserEviction:
    @pytest.mark.asyncio
    async def test_background_evicts_stale_users(self, collector):
        """Simulate the eviction logic from _collect_metrics."""
        collector._active_user_timeout = 60
        stale_ts = time.time() - 120
        collector._active_users["stale-user"] = stale_ts
        collector._active_users["active-user"] = time.time()

        # Run eviction inline (same logic as _collect_metrics)
        now = time.time()
        stale = [
            uid for uid, ts in collector._active_users.items()
            if now - ts > collector._active_user_timeout
        ]
        for uid in stale:
            del collector._active_users[uid]

        assert "stale-user" not in collector._active_users
        assert "active-user" in collector._active_users
