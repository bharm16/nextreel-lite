"""Tests for infra.pool atexit cleanup handler."""
import logging
from unittest.mock import AsyncMock, patch

from infra.pool import PoolState, SecureConnectionPool, _cleanup_pool_sync


def _make_pool_with_state(state: PoolState) -> SecureConnectionPool:
    pool = SecureConnectionPool.__new__(SecureConnectionPool)
    pool.state = state
    return pool


def test_update_state_from_usage_healthy():
    pool = _make_pool_with_state(PoolState.CRITICAL)
    pool.update_state_from_usage(free=8, size=10)
    assert pool.state == PoolState.HEALTHY


def test_update_state_from_usage_degraded():
    pool = _make_pool_with_state(PoolState.HEALTHY)
    pool.update_state_from_usage(free=2, size=10)
    assert pool.state == PoolState.DEGRADED


def test_update_state_from_usage_critical_when_usage_above_90():
    pool = _make_pool_with_state(PoolState.HEALTHY)
    pool.update_state_from_usage(free=0, size=10)
    assert pool.state == PoolState.CRITICAL


def test_update_state_from_usage_size_zero_preserves_legacy_healthy():
    # Preserves legacy behavior from PoolHealthMonitor._run(): when size==0
    # the usage_percent fallback is 0, mapping to HEALTHY.
    pool = _make_pool_with_state(PoolState.HEALTHY)
    pool.update_state_from_usage(free=0, size=0)
    assert pool.state == PoolState.HEALTHY


def test_update_state_from_usage_noop_when_unchanged(caplog):
    pool = _make_pool_with_state(PoolState.HEALTHY)
    with caplog.at_level(logging.INFO, logger="infra.pool"):
        pool.update_state_from_usage(free=8, size=10)
        pool.update_state_from_usage(free=9, size=10)
        pool.update_state_from_usage(free=10, size=10)
    transition_logs = [r for r in caplog.records if "transition" in r.message.lower()]
    assert transition_logs == []
    assert pool.state == PoolState.HEALTHY


def test_update_state_from_usage_logs_on_transition(caplog):
    pool = _make_pool_with_state(PoolState.HEALTHY)
    with caplog.at_level(logging.INFO, logger="infra.pool"):
        pool.update_state_from_usage(free=0, size=10)
    assert pool.state == PoolState.CRITICAL
    assert any("transition" in r.message.lower() for r in caplog.records)


def test_cleanup_pool_sync_no_running_loop_calls_asyncio_run():
    """When no loop is running (normal atexit), use asyncio.run."""
    with patch("infra.pool._pool", object()):
        with patch("infra.pool.close_pool", new=AsyncMock()):
            with patch(
                "infra.pool.asyncio.get_running_loop",
                side_effect=RuntimeError("no running loop"),
            ):
                with patch("infra.pool.asyncio.run") as mock_run:
                    _cleanup_pool_sync()
                    mock_run.assert_called_once()


def test_cleanup_pool_sync_with_running_loop_skips_cleanup():
    """When a loop IS running, log and return instead of crashing."""
    with patch("infra.pool._pool", object()):
        with patch("infra.pool.asyncio.get_running_loop", return_value=object()):
            with patch("infra.pool.asyncio.run") as mock_run:
                _cleanup_pool_sync()
                mock_run.assert_not_called()
