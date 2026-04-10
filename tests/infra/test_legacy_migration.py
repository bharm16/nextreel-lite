"""Tests for ``infra.legacy_migration`` caching behavior."""

from __future__ import annotations

import pytest

from infra import legacy_migration
from infra.legacy_migration import LegacyMigrationHelper, _reset_dual_write_cache


class _FakePool:
    def __init__(self):
        self.calls = 0

    async def execute(self, *args, **kwargs):
        self.calls += 1
        # First call: SELECT migration_started_at -> return a long-ago date so
        # the helper falls through to the second SELECT.
        # Simpler: always return the started_at row so we hit the early-return
        # branch (within min_days window -> True). That's exactly 1 SELECT.
        # Naive ISO matches infra.time_utils.utcnow() (naive datetimes for
        # MySQL compatibility). Far-future date keeps us inside the
        # min-days window so dual_write_enabled returns True after one SELECT.
        return {"meta_value": "2099-01-01T00:00:00"}


@pytest.mark.asyncio
async def test_dual_write_enabled_caches_within_ttl(monkeypatch):
    _reset_dual_write_cache()
    monkeypatch.setenv("NAV_STATE_DUAL_WRITE_ENABLED", "true")

    pool = _FakePool()
    helper = LegacyMigrationHelper(pool)

    result1 = await helper.dual_write_enabled()
    calls_after_first = pool.calls
    result2 = await helper.dual_write_enabled()
    result3 = await helper.dual_write_enabled()

    assert result1 is True
    assert result2 == result1
    assert result3 == result1
    # Subsequent calls hit the cache — no further DB calls.
    assert pool.calls == calls_after_first


@pytest.mark.asyncio
async def test_dual_write_enabled_cache_expires(monkeypatch):
    _reset_dual_write_cache()
    monkeypatch.setenv("NAV_STATE_DUAL_WRITE_ENABLED", "true")

    pool = _FakePool()
    helper = LegacyMigrationHelper(pool)

    await helper.dual_write_enabled()
    calls_after_first = pool.calls

    # Simulate cache expiry by clearing it.
    _reset_dual_write_cache()

    await helper.dual_write_enabled()
    assert pool.calls > calls_after_first


@pytest.mark.asyncio
async def test_dual_write_enabled_env_false_short_circuits(monkeypatch):
    _reset_dual_write_cache()
    monkeypatch.setenv("NAV_STATE_DUAL_WRITE_ENABLED", "false")

    pool = _FakePool()
    helper = LegacyMigrationHelper(pool)

    result = await helper.dual_write_enabled()
    assert result is False
    # Cached subsequent call still returns False with no DB hit.
    assert pool.calls == 0
    result2 = await helper.dual_write_enabled()
    assert result2 is False
    assert pool.calls == 0
