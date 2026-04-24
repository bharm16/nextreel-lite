"""Tests for the extracted pool monitor components."""

import asyncio

import pytest

from infra.pool_monitors import PoolCircuitBreaker, SlowQueryLogger


# ── PoolCircuitBreaker ───────────────────────────────────────────────


async def test_circuit_breaker_starts_closed():
    cb = PoolCircuitBreaker(threshold=3)
    assert cb.state == "closed"
    assert await cb.can_attempt() is True


async def test_circuit_breaker_opens_after_threshold():
    cb = PoolCircuitBreaker(threshold=3, timeout=30)
    for _ in range(3):
        await cb.record_failure()
    assert cb.state == "open"
    assert cb.trips == 1


async def test_circuit_breaker_blocks_when_open():
    cb = PoolCircuitBreaker(threshold=1, timeout=9999)
    await cb.record_failure()
    assert cb.state == "open"
    assert await cb.can_attempt() is False


async def test_circuit_breaker_resets_on_success():
    cb = PoolCircuitBreaker(threshold=3, timeout=0)
    for _ in range(3):
        await cb.record_failure()
    # timeout=0 means it should transition to half-open immediately
    assert await cb.can_attempt() is True
    assert cb.state == "half-open"
    await cb.record_success()
    assert cb.state == "closed"
    assert cb.failures == 0


async def test_circuit_breaker_reset():
    cb = PoolCircuitBreaker(threshold=1)
    await cb.record_failure()
    assert cb.state == "open"
    await cb.reset()
    assert cb.state == "closed"
    assert cb.failures == 0


# ── SlowQueryLogger ─────────────────────────────────────────────────


async def test_slow_query_logger_skips_non_select():
    # Should not raise — silently skips non-SELECT
    await SlowQueryLogger.log_explain(None, "INSERT INTO foo VALUES (1)", None)


async def test_slow_query_logger_handles_errors_gracefully():
    class FakeConn:
        def cursor(self):
            raise RuntimeError("no cursor")

    # Should not raise — errors are swallowed
    await SlowQueryLogger.log_explain(FakeConn(), "SELECT 1", None)
