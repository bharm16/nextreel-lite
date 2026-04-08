# DB Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Each task uses checkbox (`- [ ]`) syntax. **Subagents STOP AT THE DIFF — do NOT commit.** User commits each task between reviews.

**Goal:** Ship 4 targeted DB hardening changes: add two missing indexes, batch an unbounded UPDATE, and keep the ops SQL in sync.

**Branch:** `refactor/general-cleanup-v2` (same branch as the prior scale-hardening pass).

**Architecture:** All changes are additive and backward-compatible. Two runtime-schema migrations patterned on existing helpers, one loop refactor in projection_store, one ops SQL update, one docs update.

**Tech stack:** MySQL (InnoDB), aiomysql pool, pytest-asyncio.

---

## Context

See the design spec at `docs/superpowers/specs/2026-04-07-db-hardening-design.md` for full rationale and out-of-scope decisions. TL;DR:

1. `movie_candidates.shuffle_key` has no index but is the primary sort column in the hot candidate-fetch query (`movies/candidate_store.py:147`).
2. `ProjectionStore.requeue_stale_projections` (`movies/projection_store.py:357-370`) does an unbounded UPDATE that locks potentially thousands of rows.
3. `popular_movies_cache` (defined in `ops/production_db_optimization.sql:79-102`) has only `idx_cache_rand (rand_order)` — filter+rand queries can't use both.
4. `ops/production_db_optimization.sql` must be updated so fresh deploys match the runtime migration.

---

## File Structure

**Files modified:**
- `infra/runtime_schema.py` — add `ensure_movie_candidates_shuffle_key_index()` and `ensure_popular_movies_cache_composite_index()`, wire into `ensure_runtime_schema()`
- `movies/projection_store.py` — refactor `requeue_stale_projections` to loop with `LIMIT`
- `ops/production_db_optimization.sql` — add composite index to CREATE TABLE + refresh procedure
- `CLAUDE.md` — document new indexes + dual-write operational note

**Files not created:** All changes live in existing modules.

**Tests modified/created:**
- `tests/test_runtime_schema.py` — add tests for the two new migration helpers
- `tests/test_projection_store.py` — add test for batched requeue behavior

---

## Task 1: `idx_movie_candidates_shuffle` migration

**Files:**
- Modify: `infra/runtime_schema.py`
- Modify: `tests/test_runtime_schema.py`

**Context for implementer:** The existing helper `ensure_movie_candidates_refreshed_at_index` at `infra/runtime_schema.py:204-224` is the exact pattern to follow. It does an `information_schema.statistics` existence check, then a `CREATE INDEX` if missing, then logs. Copy this structure for the new helper. Wire the new helper into `ensure_runtime_schema()` at line 100 alongside the other ensure_ calls.

The new index should be `idx_movie_candidates_shuffle` on columns `(shuffle_key, numVotes, averageRating)`. MySQL does not support per-column DESC in index keys before 8.0, so we omit direction — MySQL will use the index for the leading-column sort and filesort only the tail.

- [ ] **Step 1: Read context**

Read `infra/runtime_schema.py` lines 1-250 to see the existing helpers and the `ensure_runtime_schema()` top-level function.
Read `tests/test_runtime_schema.py` lines 1-150 to see the `mock_db_pool` fixture and existing test conventions.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_runtime_schema.py`:

```python
async def test_ensure_movie_candidates_shuffle_key_index_adds_when_missing(mock_db_pool):
    mock_db_pool.execute.side_effect = [None, None]

    from infra.runtime_schema import ensure_movie_candidates_shuffle_key_index
    await ensure_movie_candidates_shuffle_key_index(mock_db_pool)

    assert mock_db_pool.execute.await_count == 2
    lookup_query = mock_db_pool.execute.await_args_list[0].args[0]
    create_query = mock_db_pool.execute.await_args_list[1].args[0]
    assert "information_schema.statistics" in lookup_query
    assert "idx_movie_candidates_shuffle" in lookup_query
    assert "CREATE INDEX idx_movie_candidates_shuffle" in create_query
    assert "(shuffle_key, numVotes, averageRating)" in create_query


async def test_ensure_movie_candidates_shuffle_key_index_skips_when_present(mock_db_pool):
    mock_db_pool.execute.return_value = {"present": 1}

    from infra.runtime_schema import ensure_movie_candidates_shuffle_key_index
    await ensure_movie_candidates_shuffle_key_index(mock_db_pool)

    mock_db_pool.execute.assert_awaited_once()
    assert "CREATE INDEX" not in mock_db_pool.execute.await_args.args[0]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `source venv/bin/activate && python3 -m pytest tests/test_runtime_schema.py::test_ensure_movie_candidates_shuffle_key_index_adds_when_missing tests/test_runtime_schema.py::test_ensure_movie_candidates_shuffle_key_index_skips_when_present -v`
Expected: FAIL (function does not exist).

- [ ] **Step 4: Implement the migration helper**

In `infra/runtime_schema.py`, add a new helper immediately after `ensure_movie_candidates_refreshed_at_index` (around line 225):

```python
async def ensure_movie_candidates_shuffle_key_index(db_pool) -> None:
    """Ensure shuffle_key has an index to support the hot candidate-fetch sort.

    movies/candidate_store.py orders candidate queries by
    (shuffle_key, numVotes DESC, averageRating DESC). Without this index
    MySQL filesorts on every fetch.
    """
    present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'movie_candidates'
          AND index_name = 'idx_movie_candidates_shuffle'
        LIMIT 1
        """,
        fetch="one",
    )
    if present:
        return

    await db_pool.execute(
        "CREATE INDEX idx_movie_candidates_shuffle "
        "ON movie_candidates (shuffle_key, numVotes, averageRating)",
        fetch="none",
    )
    logger.info("Added movie_candidates shuffle_key index")
```

- [ ] **Step 5: Wire into `ensure_runtime_schema()`**

In `infra/runtime_schema.py` `ensure_runtime_schema()` (around line 100-108), add a call to the new helper directly after `ensure_movie_candidates_refreshed_at_index`:

```python
async def ensure_runtime_schema(db_pool) -> None:
    """Create runtime-owned tables if they do not already exist."""
    for statement in _RUNTIME_SCHEMA_STATEMENTS:
        await db_pool.execute(statement, fetch="none")
    await ensure_user_navigation_current_ref_column(db_pool)
    await ensure_movie_candidates_shuffle_key(db_pool)
    await ensure_movie_candidates_refreshed_at_index(db_pool)
    await ensure_movie_candidates_shuffle_key_index(db_pool)
    await ensure_user_navigation_user_id_column(db_pool)
    logger.info("Runtime schema ensured")
```

- [ ] **Step 6: Update the aggregate test for ensure_runtime_schema**

`tests/test_runtime_schema.py` has `test_ensure_runtime_schema_runs_additive_repairs_without_blocking_fulltext` around line 82. It patches each ensure_ helper and asserts call counts. Add a patch for the new helper and its assertion:

```python
# Inside the with patch(...) chain:
), patch(
    "infra.runtime_schema.ensure_movie_candidates_shuffle_key_index", AsyncMock()
) as ensure_shuffle_idx:
    await ensure_runtime_schema(mock_db_pool)

# After existing asserts:
ensure_shuffle_idx.assert_awaited_once_with(mock_db_pool)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `source venv/bin/activate && python3 -m pytest tests/test_runtime_schema.py -v`
Expected: all PASS (new + pre-existing).

- [ ] **Step 8: Stop at the diff — do NOT commit.**

---

## Task 2: Batch `requeue_stale_projections` with LIMIT loop

**Files:**
- Modify: `movies/projection_store.py:357-370`
- Modify: `tests/test_projection_store.py`

**Context for implementer:** The current implementation is a single unbounded UPDATE. After a long outage or the first run after a deploy, this can lock thousands of rows at once. Fix: loop `UPDATE ... LIMIT 500` until affected rows is less than the batch size. Return the total affected. Add a safety cap `max_iterations = 100` so an infinite loop can't form if the DB returns nonsense.

IMPORTANT: `db_pool.execute(..., fetch="none")` returns an `int` (row count) on MySQL via aiomysql. Verify this by looking at how `execute_secure()` handles `fetch="none"` in `infra/pool.py:293` — `result = cursor.rowcount`. Use that to tell whether another batch is needed.

- [ ] **Step 1: Read context**

Read `movies/projection_store.py` lines 340-371 and `tests/test_projection_store.py` (find existing tests for `requeue_stale_projections`).

- [ ] **Step 2: Write the failing test**

Append to `tests/test_projection_store.py` (match the existing fixture style for `mock_db_pool`):

```python
async def test_requeue_stale_projections_batches_under_limit():
    from movies.projection_store import ProjectionStore

    mock_pool = AsyncMock()
    # Simulate: first batch affects 500 (full batch), second affects 150, loop exits.
    mock_pool.execute = AsyncMock(side_effect=[500, 150])

    store = ProjectionStore(mock_pool)
    total = await store.requeue_stale_projections()

    assert total == 650
    assert mock_pool.execute.await_count == 2
    # Each call must include LIMIT in the query.
    for call in mock_pool.execute.await_args_list:
        query = call.args[0]
        assert "LIMIT" in query.upper()


async def test_requeue_stale_projections_exits_immediately_when_empty():
    from movies.projection_store import ProjectionStore

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=0)

    store = ProjectionStore(mock_pool)
    total = await store.requeue_stale_projections()

    assert total == 0
    assert mock_pool.execute.await_count == 1


async def test_requeue_stale_projections_safety_cap_prevents_infinite_loop():
    """If the DB never returns less than batch size, loop must exit after max_iterations."""
    from movies.projection_store import ProjectionStore

    mock_pool = AsyncMock()
    # Always return a full batch (pathological: rows keep appearing).
    mock_pool.execute = AsyncMock(return_value=500)

    store = ProjectionStore(mock_pool)
    total = await store.requeue_stale_projections()

    # Safety cap is 100 iterations → 50000 rows max.
    assert mock_pool.execute.await_count == 100
    assert total == 50000
```

Imports at top of the test file should already have `AsyncMock`. Add if missing.

- [ ] **Step 3: Run tests to verify they fail**

Run: `source venv/bin/activate && python3 -m pytest tests/test_projection_store.py::test_requeue_stale_projections_batches_under_limit tests/test_projection_store.py::test_requeue_stale_projections_exits_immediately_when_empty tests/test_projection_store.py::test_requeue_stale_projections_safety_cap_prevents_infinite_loop -v`
Expected: FAIL.

- [ ] **Step 4: Implement the batched UPDATE**

In `movies/projection_store.py`, replace `requeue_stale_projections` (lines 357-370) with:

```python
async def requeue_stale_projections(self, batch_size: int = 500) -> int:
    """Mark ready projections past their staleness window as stale.

    Loops UPDATE ... LIMIT batch_size until affected rows < batch_size to
    bound InnoDB row-lock hold time. Safety cap of 100 iterations prevents
    pathological infinite loops.
    """
    max_iterations = 100
    total_affected = 0
    for _ in range(max_iterations):
        now = utcnow()
        affected = await self.db_pool.execute(
            """
            UPDATE movie_projection
            SET projection_state = %s
            WHERE projection_state = %s
              AND stale_after IS NOT NULL
              AND stale_after <= %s
            LIMIT %s
            """,
            [PROJECTION_STALE, PROJECTION_READY, now, batch_size],
            fetch="none",
        )
        affected_count = affected if isinstance(affected, int) else 0
        total_affected += affected_count
        if affected_count < batch_size:
            break
    return total_affected
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `source venv/bin/activate && python3 -m pytest tests/test_projection_store.py -v`
Expected: all new tests PASS. Any pre-existing test for `requeue_stale_projections` that asserted on a single execute call may now fail — update it mechanically to match the new loop shape.

- [ ] **Step 6: Verify the worker cron job still wires correctly**

Run: `source venv/bin/activate && python3 -m pytest tests/test_worker.py -v`
Expected: all PASS (the worker just calls `requeue_stale_projections()` with no args, and `batch_size=500` is the default).

- [ ] **Step 7: Stop at the diff — do NOT commit.**

---

## Task 3: `idx_cache_filter_rand` conditional migration

**Files:**
- Modify: `infra/runtime_schema.py`
- Modify: `tests/test_runtime_schema.py`

**Context for implementer:** `popular_movies_cache` is defined in `ops/production_db_optimization.sql`, NOT in `infra/runtime_schema.py`. That means dev environments without the ops script never have this table. The migration must be CONDITIONAL on the table's existence — use `information_schema.TABLES` to check. If the table is missing, log a debug message and return without error.

The new index covers `(startYear, averageRating, numVotes, rand_order)` — filter prefix + rand suffix, so filtered + random-sorted queries in `movies/query_builder.py:414-415` can use a single index.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime_schema.py`:

```python
async def test_ensure_popular_movies_cache_composite_index_skips_when_table_missing(mock_db_pool):
    mock_db_pool.execute.return_value = None  # table existence check returns nothing

    from infra.runtime_schema import ensure_popular_movies_cache_composite_index
    await ensure_popular_movies_cache_composite_index(mock_db_pool)

    # Should check table existence once, then return without creating anything.
    assert mock_db_pool.execute.await_count == 1
    lookup_query = mock_db_pool.execute.await_args.args[0]
    assert "information_schema.tables" in lookup_query.lower()


async def test_ensure_popular_movies_cache_composite_index_skips_when_present(mock_db_pool):
    # First call: table exists. Second call: index exists.
    mock_db_pool.execute.side_effect = [{"present": 1}, {"present": 1}]

    from infra.runtime_schema import ensure_popular_movies_cache_composite_index
    await ensure_popular_movies_cache_composite_index(mock_db_pool)

    assert mock_db_pool.execute.await_count == 2
    # No CREATE INDEX should have been issued.
    for call in mock_db_pool.execute.await_args_list:
        assert "CREATE INDEX" not in call.args[0]


async def test_ensure_popular_movies_cache_composite_index_creates_when_missing(mock_db_pool):
    # First call: table exists. Second call: index missing. Third call: CREATE INDEX.
    mock_db_pool.execute.side_effect = [{"present": 1}, None, None]

    from infra.runtime_schema import ensure_popular_movies_cache_composite_index
    await ensure_popular_movies_cache_composite_index(mock_db_pool)

    assert mock_db_pool.execute.await_count == 3
    create_query = mock_db_pool.execute.await_args_list[2].args[0]
    assert "CREATE INDEX idx_cache_filter_rand" in create_query
    assert "(startYear, averageRating, numVotes, rand_order)" in create_query
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && python3 -m pytest tests/test_runtime_schema.py -v -k popular_movies_cache_composite`
Expected: FAIL.

- [ ] **Step 3: Implement the helper**

In `infra/runtime_schema.py`, add after `ensure_movie_candidates_shuffle_key_index`:

```python
async def ensure_popular_movies_cache_composite_index(db_pool) -> None:
    """Add a filter+rand composite index to popular_movies_cache if it exists.

    popular_movies_cache is defined in ops/production_db_optimization.sql
    and may not exist in dev environments. We check table presence first
    so dev bootstraps are no-ops.
    """
    table_present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = 'popular_movies_cache'
        LIMIT 1
        """,
        fetch="one",
    )
    if not table_present:
        logger.debug("popular_movies_cache not present; skipping composite index")
        return

    index_present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'popular_movies_cache'
          AND index_name = 'idx_cache_filter_rand'
        LIMIT 1
        """,
        fetch="one",
    )
    if index_present:
        return

    await db_pool.execute(
        "CREATE INDEX idx_cache_filter_rand "
        "ON popular_movies_cache (startYear, averageRating, numVotes, rand_order)",
        fetch="none",
    )
    logger.info("Added popular_movies_cache composite filter+rand index")
```

- [ ] **Step 4: Wire into `ensure_runtime_schema()`**

Add the call after `ensure_movie_candidates_shuffle_key_index(db_pool)`:

```python
await ensure_movie_candidates_shuffle_key_index(db_pool)
await ensure_popular_movies_cache_composite_index(db_pool)
await ensure_user_navigation_user_id_column(db_pool)
```

- [ ] **Step 5: Update the aggregate ensure_runtime_schema test**

Add to the `with patch(...)` chain in `test_ensure_runtime_schema_runs_additive_repairs_without_blocking_fulltext`:

```python
), patch(
    "infra.runtime_schema.ensure_popular_movies_cache_composite_index", AsyncMock()
) as ensure_cache_idx:
    await ensure_runtime_schema(mock_db_pool)

# After existing asserts:
ensure_cache_idx.assert_awaited_once_with(mock_db_pool)
```

- [ ] **Step 6: Run the tests**

Run: `source venv/bin/activate && python3 -m pytest tests/test_runtime_schema.py -v`
Expected: all PASS.

- [ ] **Step 7: Stop at the diff — do NOT commit.**

---

## Task 4: Update `ops/production_db_optimization.sql`

**Files:**
- Modify: `ops/production_db_optimization.sql`

**Context for implementer:** Keep the ops SQL in sync with the runtime migration. The `popular_movies_cache` CREATE TABLE at lines 79-102 currently has only `INDEX idx_cache_rand (rand_order)`. Add the composite index alongside it. Also verify that the refresh procedure starting around line 210 re-creates the index on the "next" table before swap — if it doesn't, refreshes will silently lose the index until the next runtime bootstrap.

No tests required — this is a SQL file, tested by the runtime migration tests in Task 3.

- [ ] **Step 1: Read context**

Read `ops/production_db_optimization.sql` in full (it's ~300 lines). Pay attention to:
- The CREATE TABLE for `popular_movies_cache` (around line 79-102)
- The refresh procedure `refresh_movie_caches()` (around line 210+)
- Whether `popular_movies_cache_next` is created via `LIKE popular_movies_cache` (which copies indexes) or via a separate CREATE TABLE (which doesn't)

- [ ] **Step 2: Add the composite index to the CREATE TABLE**

In the `popular_movies_cache` CREATE TABLE block (~line 79-102), add a new INDEX line alongside the existing `INDEX idx_cache_rand (rand_order)`:

```sql
INDEX idx_cache_filter_rand (startYear, averageRating, numVotes, rand_order)
```

Place it directly below the `idx_cache_rand` line so the two are visually grouped. Match existing formatting.

- [ ] **Step 3: Verify the refresh procedure propagates the index**

In the refresh procedure (~line 210+), find the `CREATE TABLE popular_movies_cache_next LIKE popular_movies_cache` statement. `LIKE` preserves indexes, so nothing to change. If instead the procedure uses an explicit CREATE TABLE definition, add the new INDEX line there as well.

- [ ] **Step 4: Add a migration note as a comment**

At the top of the `popular_movies_cache` CREATE TABLE block, add a comment like:

```sql
-- idx_cache_filter_rand added 2026-04 to support filter+random queries
-- (see movies/query_builder.py:414-415). Fresh deploys get it from this
-- file; existing deploys get it via infra/runtime_schema.py.
```

- [ ] **Step 5: Sanity check the file**

Run a syntax sanity check (MySQL isn't available in the sandbox, so visual review only):
- Look for balanced parentheses in the CREATE TABLE.
- Confirm trailing commas are correct (MySQL requires no trailing comma before `)`).

Run: `source venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -10`
Expected: no regressions from prior tasks.

- [ ] **Step 6: Stop at the diff — do NOT commit.**

---

## Task 5: CLAUDE.md documentation update

**Files:**
- Modify: `CLAUDE.md`

**Context for implementer:** Document the new indexes and the dual-write operational note. Small docs change. No tests.

- [ ] **Step 1: Read current CLAUDE.md content near the SQL section**

Read `CLAUDE.md` lines ~120-160 (the "SQL" subsection under "Key Patterns").

- [ ] **Step 2: Add a note under Gotchas about the new indexes**

Append to the Gotchas section a new bullet:

```markdown
- **Runtime-created indexes**: `infra/runtime_schema.py` adds three indexes at startup that aren't in the base CREATE TABLE: `idx_movie_candidates_refreshed_at`, `idx_movie_candidates_shuffle` (supports the hot candidate-fetch ORDER BY at `movies/candidate_store.py:147`), and `idx_cache_filter_rand` on `popular_movies_cache` (supports filter+random queries at `movies/query_builder.py:414-415`, conditional on the ops table existing).
```

- [ ] **Step 3: Add a dual-write operational note**

In the Environment section (around line 95 where `NAV_STATE_DUAL_WRITE_ENABLED` is already listed), extend the note:

```markdown
- `NAV_STATE_DUAL_WRITE_ENABLED` — Navigation migration dual-write (default: `true`). **Ops: flip to `false` once the 7-day migration window has elapsed** to remove write-amplification on navigation mutations.
```

- [ ] **Step 4: Stop at the diff — do NOT commit.**

---

## Verification: end-to-end after all tasks

After all 5 tasks are in the working tree:

1. **Full test suite:**
   ```bash
   source venv/bin/activate && python3 -m pytest tests/ -v 2>&1 | tail -30
   ```
   Expected: all pass except the 2 pre-existing `TestPayloadFromRow` failures that pre-date this work.

2. **Startup smoke test:**
   ```bash
   source venv/bin/activate && python3 -c "
   import asyncio
   from infra.runtime_schema import ensure_runtime_schema
   # Requires a test MySQL — skip if not available
   print('ensure_runtime_schema importable')
   "
   ```

3. **Live DB verification** (operator, against a real MySQL):
   ```sql
   SHOW INDEX FROM movie_candidates WHERE Key_name = 'idx_movie_candidates_shuffle';
   SHOW INDEX FROM popular_movies_cache WHERE Key_name = 'idx_cache_filter_rand';
   ```
   Expected: both indexes present after one app startup.

4. **EXPLAIN on the hot query:**
   ```sql
   EXPLAIN SELECT tconst, primaryTitle, slug
   FROM movie_candidates
   WHERE titleType='movie' AND startYear BETWEEN 1980 AND 2026
     AND averageRating BETWEEN 6 AND 10 AND numVotes BETWEEN 10000 AND 10000000
     AND sample_bucket IN (0,1,2)
   ORDER BY shuffle_key, numVotes DESC, averageRating DESC
   LIMIT 45;
   ```
   Expected: `Extra` column no longer says `Using filesort` (or shows `Using index condition` with the new index in `key`).

5. **Worker cron job first run:**
   ```bash
   source venv/bin/activate && arq worker.WorkerSettings
   ```
   Trigger or wait for `requeue_stale_projections`; log should report the total affected count.

---

## Execution notes for subagent-driven development

- **Model:** Tasks 1, 3, 4, 5 are mechanical (follow existing patterns, small SQL changes, docs). Use a fast model. Task 2 needs a little more care (loop logic + safety cap) — standard model.
- **Order:** Tasks 1 → 3 → 4 → 2 → 5 is a good sequence (schema migrations first, then the code change that depends on them, then docs). But the tasks are independent — any order works.
- **No autocommit:** Every task ends with "Stop at the diff — do NOT commit." User commits between tasks.
- **Pre-existing failures:** `tests/test_projection_store.py::TestPayloadFromRow::test_none_payload_returns_empty_dict_with_state` and `::test_non_dict_payload_returns_empty_dict` exist on HEAD `83a17058` and are unrelated to this work. Ignore them in sweeps.
