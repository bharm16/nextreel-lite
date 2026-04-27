"""Tests for the public_id backfill helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from infra.runtime_schema import ensure_movie_projection_public_id_backfill


class _ScriptedPool:
    """Records every execute() call in order, replaying scripted return values.

    Mirrors the ``await pool.execute(sql, params, fetch=...)`` shape used by
    the runtime_schema helpers.
    """

    def __init__(self, scripted: list):
        self._scripted = list(scripted)
        self.calls: list[tuple[str, list, str | None]] = []
        self.execute = AsyncMock(side_effect=self._next_response)

    async def _next_response(self, sql, params=None, fetch=None):
        self.calls.append((sql, params, fetch))
        if not self._scripted:
            raise AssertionError(f"Unscripted execute({sql!r})")
        item = self._scripted.pop(0)
        # Allow tests to script a raised exception (e.g. simulating a DB
        # connection drop mid-backfill) by inserting an Exception instance
        # into the scripted list — AsyncMock semantics for ``side_effect``
        # iterables that mix values and exceptions.
        if isinstance(item, BaseException):
            raise item
        return item


def _flag_done():
    """Scripted return for ``_get_runtime_flag(public_id_backfill_done)`` = truthy."""
    return {"meta_value": "1"}


def _flag_not_done():
    """Scripted return for ``_get_runtime_flag`` = no row / not yet recorded."""
    return None


def _lock_acquired():
    return {"locked": 1}


def _lock_not_acquired():
    return {"locked": 0}


async def test_short_circuits_when_already_done():
    """If the runtime_metadata flag is set, the helper does no work — no lock taken."""
    pool = _ScriptedPool(scripted=[_flag_done()])

    await ensure_movie_projection_public_id_backfill(pool)

    assert pool.execute.await_count == 1


async def test_short_circuits_when_lock_held_by_other_replica():
    """A non-acquired GET_LOCK means another replica owns the backfill — skip silently."""
    pool = _ScriptedPool(
        scripted=[
            _flag_not_done(),
            _lock_not_acquired(),
        ]
    )

    await ensure_movie_projection_public_id_backfill(pool)

    # No further work, no RELEASE_LOCK either (we never acquired it).
    assert pool.execute.await_count == 2
    assert "GET_LOCK" in pool.calls[1][0]


async def test_backfills_null_rows_then_tightens_column():
    """With NULL rows, helper invokes assign_public_id per row, then ALTERs and records flag.

    ``assign_public_id`` is patched so the test focuses on the backfill
    loop's flow control. Per-row UPDATE calls do NOT show up in
    ``pool.calls`` — only the helper's own SQL does.
    """
    pool = _ScriptedPool(
        scripted=[
            _flag_not_done(),                                           # initial flag check
            _lock_acquired(),                                            # GET_LOCK
            _flag_not_done(),                                            # re-check after lock
            [{"tconst": "tt0000001"}, {"tconst": "tt0000002"}],          # first SELECT chunk (< CHUNK_SIZE → loop exits)
            None,                                                        # remaining-NULLs probe → no rows
            None,                                                        # ALTER MODIFY COLUMN
            None,                                                        # _set_runtime_flag insert
            {"released": 1},                                             # RELEASE_LOCK
        ]
    )

    fake_assign = AsyncMock(side_effect=["abc123", "def456"])
    with patch("movies.public_id.assign_public_id", fake_assign):
        await ensure_movie_projection_public_id_backfill(pool)

    assert fake_assign.await_count == 2
    fake_assign.assert_any_await(pool, "tt0000001")
    fake_assign.assert_any_await(pool, "tt0000002")

    sqls = [call[0] for call in pool.calls]
    assert pool.execute.await_count == 8
    assert "FROM runtime_metadata" in sqls[0]
    assert "GET_LOCK" in sqls[1]
    assert "FROM runtime_metadata" in sqls[2]
    assert "FROM movie_projection" in sqls[3] and "public_id IS NULL" in sqls[3]
    assert "LIMIT" in sqls[3]  # cursor-paginated
    assert "WHERE public_id IS NULL LIMIT 1" in sqls[4]  # remaining probe
    assert "MODIFY COLUMN public_id CHAR(6) NOT NULL" in sqls[5]
    assert "INSERT INTO runtime_metadata" in sqls[6]
    assert "RELEASE_LOCK" in sqls[7]


async def test_no_null_rows_still_tightens_and_records_flag():
    pool = _ScriptedPool(
        scripted=[
            _flag_not_done(),
            _lock_acquired(),
            _flag_not_done(),
            [],          # SELECT returns no NULL rows → loop exits immediately
            None,        # remaining probe → no rows
            None,        # ALTER MODIFY COLUMN
            None,        # _set_runtime_flag
            {"released": 1},
        ]
    )

    fake_assign = AsyncMock()
    with patch("movies.public_id.assign_public_id", fake_assign):
        await ensure_movie_projection_public_id_backfill(pool)

    assert fake_assign.await_count == 0
    assert pool.execute.await_count == 8
    assert "MODIFY COLUMN public_id CHAR(6) NOT NULL" in pool.calls[5][0]


async def test_resumes_after_partial_completion():
    """A previously-interrupted run left some NULLs behind; this run finishes them.

    Simulates the resumption path: the helper is invoked, the flag is still
    not set (prior run aborted before flagging), the SELECT now picks up
    only the still-NULL tconsts (the WHERE filter handles that naturally),
    assigns IDs to all of them, and on the post-loop probe finds no NULLs
    remaining → ALTER + flag fire and the run completes.
    """
    pool = _ScriptedPool(
        scripted=[
            _flag_not_done(),
            _lock_acquired(),
            _flag_not_done(),
            # Only the previously-skipped rows come back from the SELECT —
            # the WHERE public_id IS NULL filter excludes already-assigned ones.
            [{"tconst": "tt0000003"}, {"tconst": "tt0000004"}],
            None,                # remaining probe → no rows
            None,                # ALTER
            None,                # set flag
            {"released": 1},
        ]
    )

    fake_assign = AsyncMock(side_effect=["xxx111", "yyy222"])
    with patch("movies.public_id.assign_public_id", fake_assign):
        await ensure_movie_projection_public_id_backfill(pool)

    # Both remaining rows were processed and the flag was set this run.
    assert fake_assign.await_count == 2
    sqls = [call[0] for call in pool.calls]
    assert "MODIFY COLUMN public_id CHAR(6) NOT NULL" in sqls[5]
    assert "INSERT INTO runtime_metadata" in sqls[6]


async def test_per_row_failure_skips_row_and_does_not_brick_run():
    """A single ``assign_public_id`` exception is logged + skipped, not raised.

    The cursor still advances so the loop doesn't spin on a stuck row.
    The post-loop probe finds the failed row still NULL, so the ALTER and
    flag are deferred — next startup will retry.
    """
    pool = _ScriptedPool(
        scripted=[
            _flag_not_done(),
            _lock_acquired(),
            _flag_not_done(),
            [{"tconst": "tt_ok"}, {"tconst": "tt_fail"}],
            # Probe finds tt_fail still NULL → defer ALTER+flag.
            {"meta_value": 1},  # truthy means "row exists" for the probe
            {"released": 1},    # RELEASE_LOCK
        ]
    )

    fake_assign = AsyncMock(
        side_effect=["okokok", RuntimeError("simulated DB blip")]
    )
    with patch("movies.public_id.assign_public_id", fake_assign):
        # Must NOT raise — per-row failure is swallowed.
        await ensure_movie_projection_public_id_backfill(pool)

    assert fake_assign.await_count == 2
    sqls = [call[0] for call in pool.calls]
    # No ALTER and no INSERT INTO runtime_metadata — the flag stays unset
    # and next startup retries.
    assert not any("MODIFY COLUMN public_id" in s for s in sqls)
    assert not any("INSERT INTO runtime_metadata" in s for s in sqls)
    # Lock was still released even though we didn't ALTER.
    assert "RELEASE_LOCK" in sqls[-1]


async def test_lock_released_even_if_assign_raises_unexpectedly():
    """A truly-unexpected exception (not caught per-row) still releases the lock."""
    pool = _ScriptedPool(
        scripted=[
            _flag_not_done(),
            _lock_acquired(),
            _flag_not_done(),
            # SELECT raises — bubbles out of the try/finally
        ]
    )
    pool._scripted.append(RuntimeError("SELECT exploded"))
    pool._scripted.append({"released": 1})  # RELEASE_LOCK in finally

    import pytest
    with pytest.raises(RuntimeError, match="SELECT exploded"):
        await ensure_movie_projection_public_id_backfill(pool)

    sqls = [call[0] for call in pool.calls]
    assert "RELEASE_LOCK" in sqls[-1]
