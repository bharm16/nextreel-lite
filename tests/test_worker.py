"""Tests for worker.py — ARQ worker entrypoint for runtime maintenance jobs."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# startup / shutdown lifecycle
# ---------------------------------------------------------------------------


async def test_startup_initializes_context():
    """startup() should create db_pool, candidate_store, projection_store in ctx."""
    mock_pool = AsyncMock()
    mock_pool.init_pool = AsyncMock()

    with (
        patch("worker.Config") as mock_config,
        patch("worker.DatabaseConnectionPool", return_value=mock_pool) as mock_dcp,
        patch("worker.CandidateStore") as mock_cs,
        patch("worker.ProjectionStore") as mock_ps,
    ):
        mock_config.get_db_config.return_value = {"host": "localhost"}

        from worker import startup

        ctx = {}
        await startup(ctx)

        mock_config.get_db_config.assert_called_once()
        mock_dcp.assert_called_once_with({"host": "localhost"})
        mock_pool.init_pool.assert_awaited_once()
        assert ctx["db_pool"] is mock_pool
        mock_cs.assert_called_once_with(mock_pool)
        mock_ps.assert_called_once_with(mock_pool)
        assert ctx["candidate_store"] is mock_cs.return_value
        assert ctx["projection_store"] is mock_ps.return_value
        assert ctx["projection_coordinator"] is mock_ps.return_value.coordinator


async def test_shutdown_closes_db_pool():
    """shutdown() should close the pool when present in ctx."""
    mock_pool = AsyncMock()
    mock_pool.close_pool = AsyncMock()
    mock_projection_store = MagicMock()
    mock_projection_store.coordinator = AsyncMock()
    mock_projection_store.coordinator.aclose = AsyncMock()

    from worker import shutdown

    ctx = {"db_pool": mock_pool, "projection_store": mock_projection_store}
    await shutdown(ctx)

    mock_projection_store.coordinator.aclose.assert_awaited_once()
    mock_pool.close_pool.assert_awaited_once()


async def test_shutdown_handles_missing_db_pool():
    """shutdown() should not raise when db_pool is absent from ctx."""
    from worker import shutdown

    ctx = {}
    await shutdown(ctx)  # Should not raise


async def test_shutdown_handles_none_db_pool():
    """shutdown() should not raise when db_pool is None."""
    from worker import shutdown

    ctx = {"db_pool": None}
    await shutdown(ctx)  # Should not raise


# ---------------------------------------------------------------------------
# Job delegation functions
# ---------------------------------------------------------------------------


async def test_refresh_movie_candidates_delegates():
    """refresh_movie_candidates() should delegate to candidate_store."""
    from worker import refresh_movie_candidates

    mock_store = AsyncMock()
    ctx = {"candidate_store": mock_store}

    await refresh_movie_candidates(ctx)

    mock_store.refresh_movie_candidates.assert_awaited_once()


async def test_ensure_core_projection_delegates():
    """ensure_core_projection() should delegate to projection_store and return result."""
    from worker import ensure_core_projection

    mock_store = AsyncMock()
    mock_store.ensure_core_projection = AsyncMock(return_value={"tconst": "tt1234567"})
    ctx = {"projection_store": mock_store}

    result = await ensure_core_projection(ctx, "tt1234567")

    mock_store.ensure_core_projection.assert_awaited_once_with("tt1234567")
    assert result == {"tconst": "tt1234567"}


async def test_enrich_projection_delegates():
    """enrich_projection() should delegate to projection_store and return result."""
    from worker import enrich_projection

    mock_store = AsyncMock()
    mock_store.enrich_projection = AsyncMock(return_value="ready")
    ctx = {"projection_store": mock_store}

    result = await enrich_projection(ctx, "tt9999999")

    mock_store.enrich_projection.assert_awaited_once_with("tt9999999", known_tmdb_id=None)
    assert result == "ready"


async def test_requeue_stale_projections_delegates():
    """requeue_stale_projections() should delegate to projection_store and return result."""
    from worker import requeue_stale_projections

    mock_store = AsyncMock()
    mock_store.requeue_stale_projections = AsyncMock(return_value=5)
    ctx = {"projection_store": mock_store}

    result = await requeue_stale_projections(ctx)

    mock_store.requeue_stale_projections.assert_awaited_once()
    assert result == 5


# ---------------------------------------------------------------------------
# validate_referential_integrity
# ---------------------------------------------------------------------------


async def test_validate_referential_integrity_no_issues():
    """Should return 0 when no orphans are found."""
    from worker import validate_referential_integrity

    mock_pool = AsyncMock()
    # All checks return zero orphans
    mock_pool.execute = AsyncMock(return_value={"orphans": 0})
    ctx = {"db_pool": mock_pool}

    with patch("worker.INTEGRITY_CHECKS", [("check1", "SELECT 1"), ("check2", "SELECT 2")]):
        result = await validate_referential_integrity(ctx)

    assert result == 0


async def test_validate_referential_integrity_with_issues():
    """Should count queries that return orphans > 0."""
    from worker import validate_referential_integrity

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(side_effect=[
        {"orphans": 3},
        {"orphans": 0},
        {"orphans": 1},
    ])
    ctx = {"db_pool": mock_pool}

    checks = [("c1", "Q1"), ("c2", "Q2"), ("c3", "Q3")]
    with patch("worker.INTEGRITY_CHECKS", checks):
        result = await validate_referential_integrity(ctx)

    assert result == 2


async def test_validate_referential_integrity_none_result():
    """Should not count a check when execute returns None."""
    from worker import validate_referential_integrity

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=None)
    ctx = {"db_pool": mock_pool}

    with patch("worker.INTEGRITY_CHECKS", [("check1", "SELECT 1")]):
        result = await validate_referential_integrity(ctx)

    assert result == 0


# ---------------------------------------------------------------------------
# purge_expired_navigation_state
# ---------------------------------------------------------------------------


async def test_purge_expired_navigation_state_single_batch():
    """Should stop after one batch when batch_deleted < 1000."""
    from worker import purge_expired_navigation_state

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=42)
    ctx = {"db_pool": mock_pool}

    result = await purge_expired_navigation_state(ctx)

    assert result == 42
    mock_pool.execute.assert_awaited_once()


async def test_purge_expired_navigation_state_multiple_batches():
    """Should loop until a batch returns < 1000, summing all deletions."""
    from worker import purge_expired_navigation_state

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(side_effect=[1000, 1000, 500])
    ctx = {"db_pool": mock_pool}

    result = await purge_expired_navigation_state(ctx)

    assert result == 2500
    assert mock_pool.execute.await_count == 3


async def test_purge_expired_navigation_state_zero_deleted():
    """Should return 0 when nothing is expired."""
    from worker import purge_expired_navigation_state

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=0)
    ctx = {"db_pool": mock_pool}

    result = await purge_expired_navigation_state(ctx)

    assert result == 0
    mock_pool.execute.assert_awaited_once()


async def test_purge_expired_navigation_state_non_int_result():
    """Should treat non-int execute results as 0 deletions."""
    from worker import purge_expired_navigation_state

    mock_pool = AsyncMock()
    # execute returns something other than int (e.g. None or a dict)
    mock_pool.execute = AsyncMock(return_value=None)
    ctx = {"db_pool": mock_pool}

    result = await purge_expired_navigation_state(ctx)

    assert result == 0
    mock_pool.execute.assert_awaited_once()


async def test_purge_expired_navigation_state_exact_1000():
    """When exactly 1000 rows are deleted, should loop again."""
    from worker import purge_expired_navigation_state

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(side_effect=[1000, 0])
    ctx = {"db_pool": mock_pool}

    result = await purge_expired_navigation_state(ctx)

    assert result == 1000
    assert mock_pool.execute.await_count == 2


async def test_purge_uses_correct_sql():
    """Should pass the expected DELETE ... LIMIT 1000 SQL to execute."""
    from worker import purge_expired_navigation_state

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=0)
    ctx = {"db_pool": mock_pool}

    await purge_expired_navigation_state(ctx)

    call_args = mock_pool.execute.call_args
    sql = call_args[0][0]
    assert "DELETE FROM user_navigation_state" in sql
    assert "LIMIT 1000" in sql
    assert call_args[1]["fetch"] == "none"
