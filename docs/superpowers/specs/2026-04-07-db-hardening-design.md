# Database Hardening — Design

**Status:** Draft
**Date:** 2026-04-07
**Branch:** refactor/general-cleanup-v2

## Context

A database performance review of nextreel-lite surfaced a handful of scale risks in the MySQL-facing code paths. After verifying each finding against current HEAD, three are real and actionable without significant refactoring, and one more is a non-code operational issue that should be acknowledged but not fixed in this pass.

The sensational finding — "connection held across TMDb API call in `enrich_projection`" — turned out to be a **false alarm**. `infra/pool.py:280` `execute_secure()` scopes the `async with acquire()` block to a single query. `ProjectionEnrichmentCoordinator.enrich_projection` in `movies/projection_enrichment.py:98-124` calls `_select_row`, then TMDb (which issues its own HTTP request with no DB connection), then `_upsert_ready` / `_upsert_failed` as three independent `db_pool.execute()` calls. The connection is released between each. Dropping from scope.

The four remaining items are:

### 1. `movie_candidates.shuffle_key` has no index (HOT PATH)
`infra/runtime_schema.py:65` declares `shuffle_key INT NOT NULL` with no index. `movies/candidate_store.py:147` orders by `shuffle_key, numVotes DESC, averageRating DESC` on a multi-million row table. The existing composite `idx_movie_candidates_filter (titleType, startYear, averageRating, numVotes, sample_bucket)` cannot satisfy this ORDER BY, so MySQL filesorts every request. This is the single cheapest high-value fix in the entire codebase.

### 2. `requeue_stale_projections` does an unbounded UPDATE
`movies/projection_store.py:357-370` issues a single `UPDATE movie_projection SET projection_state = 'stale' WHERE projection_state = 'ready' AND stale_after <= NOW()` with no `LIMIT`. After a long outage or during initial backfill, this can touch thousands of rows in one statement, holding InnoDB row locks across the entire set and blocking concurrent reads on those rows. With the hourly cron job scheduled in the prior pass (Task 1 of the April 6 scale-hardening), ongoing steady-state row counts will be small, but the first run after a gap is unbounded.

### 3. `popular_movies_cache` is missing a filter+rand composite index
`ops/production_db_optimization.sql:98` defines `INDEX idx_cache_rand (rand_order)` alone. Queries in `movies/query_builder.py:414-415` combine `WHERE startYear/averageRating/numVotes` predicates with `ORDER BY rand_order LIMIT 15`. MySQL can use the rand_order index for sorting OR the filter columns for selection, not both — so a filtered query scans with one index and post-filters in memory, or vice versa. The table is ~50k rows so it isn't catastrophic, but it's cheap to fix.

Note: `popular_movies_cache` lives in `ops/production_db_optimization.sql`, not `infra/runtime_schema.py`. This means the runtime schema bootstrap doesn't own it, and a direct `CREATE INDEX` in the ops file only applies when an operator manually re-runs the script. To avoid a split-brain, we add a **conditional** migration helper that checks `information_schema.TABLES` for the table's existence before attempting the index.

### 4. Keep `ops/production_db_optimization.sql` in sync
So fresh ops deploys get the same indexes as the runtime migration adds.

## Out of scope (and why)

- **Connection-hold across TMDb.** Verified false alarm. Each `db_pool.execute` is independently scoped.
- **OFFSET random pagination on live tables (`query_builder.py:433`).** Real, but only hits the `use_cache=False` branch. Fixing it requires either keyset pagination (a larger refactor) or a generated `rand_order` column on `title.basics` (an IMDb-sourced table we shouldn't alter). Separate plan.
- **`recent_movies_cache` language index.** Table is ~5k rows; blast radius is small. Not worth the migration.
- **`NAV_STATE_DUAL_WRITE_ENABLED` amplification.** This is a runtime env var, not code. The migration window expires per `NAV_STATE_MIGRATION_MIN_DAYS=7`. Ops should set `NAV_STATE_DUAL_WRITE_ENABLED=false` once the window has passed — no code change required. Flagged only in CLAUDE.md.
- **Integrity checks full scans (`infra/integrity_checks.py`).** Invoked via `worker.py:91 validate_referential_integrity`, a worker job. Not in the request path. Low priority.
- **`candidate_store.py:244-266` validate_bucket_distribution Python-side aggregation.** Runs once per refresh, not per request. Negligible cost.

## Approach

Four additive, backward-compatible migrations. No schema renames, no column changes, no data rewrites. Each change is independently revertable and touches a single file (plus its tests).

1. **Task 1** — Add `ensure_movie_candidates_shuffle_key_index()` helper in `infra/runtime_schema.py`, modeled exactly on the existing `ensure_movie_candidates_refreshed_at_index()` pattern (lines 204-224). Wire into `ensure_runtime_schema()`.

2. **Task 2** — Modify `ProjectionStore.requeue_stale_projections` to loop `UPDATE ... LIMIT 500` until affected rows < 500. Preserve the return value (total rows updated). Add a `max_iterations` safety cap to prevent infinite loops on pathological input.

3. **Task 3** — Add `ensure_popular_movies_cache_composite_index()` helper in `infra/runtime_schema.py`. Guard on table existence via `information_schema.TABLES`. No-op if the table is absent (dev environments without the ops script). Wire into `ensure_runtime_schema()`.

4. **Task 4** — Update `ops/production_db_optimization.sql` to include the new index in the `CREATE TABLE popular_movies_cache` definition AND in any `ALTER TABLE` refresh logic. This keeps fresh ops runs in sync.

5. **Task 5** — Update CLAUDE.md to reference the new indexes in the "Key Patterns → SQL" section and note the dual-write operational toggle.

## Testing strategy

Each task gets:
- Unit test for the migration helper (pattern: mock db_pool, assert expected query fragments)
- Idempotency test (run twice, second is no-op)
- For Task 2: integration test with seeded rows asserting the loop bounds and final state
- For Task 3: conditional-presence test (table missing → no-op, table present → index created)

All existing tests in `tests/test_runtime_schema.py`, `tests/test_projection_store.py`, `tests/test_candidate_store.py` must continue to pass unchanged.

## Verification

After all tasks land, operator runs:
```sql
SHOW INDEX FROM movie_candidates WHERE Key_name = 'idx_movie_candidates_shuffle';
SHOW INDEX FROM popular_movies_cache WHERE Key_name = 'idx_cache_filter_rand';
EXPLAIN SELECT tconst FROM movie_candidates
  WHERE titleType='movie' AND startYear BETWEEN 1980 AND 2026
    AND averageRating BETWEEN 6 AND 10 AND numVotes BETWEEN 10000 AND 10000000
    AND sample_bucket IN (0,1,2)
  ORDER BY shuffle_key, numVotes DESC, averageRating DESC LIMIT 45;
```
Expected: `Using index` or `Using where; Using index`, NOT `Using filesort`.

And the worker log after the first cron firing of `requeue_stale_projections` should show batching:
```
Requeued N stale projections across M batches
```

## File structure

**Files modified:**
- `infra/runtime_schema.py` — add two migration helpers, wire into `ensure_runtime_schema`
- `movies/projection_store.py` — batch the UPDATE loop in `requeue_stale_projections`
- `ops/production_db_optimization.sql` — add the cache composite index to schema + refresh path
- `CLAUDE.md` — document new indexes and dual-write operational note

**Tests modified/created:**
- `tests/test_runtime_schema.py` — two new test functions for the migration helpers
- `tests/test_projection_store.py` — new test for batched requeue behavior
