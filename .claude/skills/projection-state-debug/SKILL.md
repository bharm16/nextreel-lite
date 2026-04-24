---
name: projection-state-debug
description: Inspect and debug the movie_projection state machine (core → ready → stale → failed) and enrichment cooldown for a specific tconst
disable-model-invocation: true
---

# Projection State Debug

User-only skill. Invoked via `/projection-state-debug <tconst>`. Walks the projection lifecycle for a given IMDb id so the user can see exactly what state a movie is in, why enrichment may be stuck, and whether the 15-min cooldown applies.

## Usage

- `/projection-state-debug tt0111161` — Inspect a specific projection row
- `/projection-state-debug tt0111161 --enqueue` — Inspect, then enqueue `enrich_projection` via arq

## Background (non-obvious)

See `movies/projection_state.py` and `movies/projection_enrichment.py`:

- States: `core` (IMDb-only) → `ready` (TMDb-enriched) → `stale` (>7 days since enrichment) → `failed` (last enrichment raised).
- Cooldown: a failed projection will not be re-enqueued for 15 minutes. This is stored on the row, not in Redis.
- Enrichment runs as an arq job (`enrich_projection`) on `worker.WorkerSettings`. If the worker is down, nothing happens and the state never advances.

## Execution

### 1. Check the projection row

```bash
mysql -e "
  SELECT tconst, state, last_enriched_at, last_failure_at, failure_count, updated_at
  FROM movie_projection
  WHERE tconst = %s
" # pass tconst via positional arg — NEVER f-string interpolate
```

(When typing the query into `mysql`, prefer the interactive prompt or `--execute` with a shell variable rather than concatenating the tconst into the SQL string — this skill exists partly to model the parameterized-placeholder rule.)

### 2. Check cooldown status

If `state = 'failed'` and `last_failure_at` is within the last 15 minutes, the projection is in cooldown and the worker will skip it. Compute:

```python
from datetime import datetime, timedelta, timezone
cooldown_ends = last_failure_at + timedelta(minutes=15)
in_cooldown = datetime.now(timezone.utc) < cooldown_ends
```

### 3. Check the arq queue

```bash
redis-cli LLEN arq:queue            # Default arq queue depth
redis-cli LRANGE arq:queue 0 10     # Peek at pending jobs
```

If the queue is long or the worker isn't running (`ps aux | grep "arq worker"`), enrichment will lag regardless of state.

### 4. Check the Redis cache

```bash
redis-cli GET "cache:movie:full:<tconst>"
```

A cache hit explains why the UI looks fine even when the projection state is `stale` or `failed` — the user is being served the cached `_full` dict. Note: full movie dicts carry the `_full: True` sentinel.

### 5. (Optional) Force re-enqueue

Only with `--enqueue`. Use the project's arq helper rather than raw Redis writes:

```python
from worker import arq_pool_from_settings  # or equivalent accessor
await pool.enqueue_job("enrich_projection", tconst, _defer_by=0)
```

Do NOT bypass the cooldown by zeroing `last_failure_at` directly — that loses diagnostic signal.

## Reporting

Summarize for the user:
- Current state + age
- Whether the row is in cooldown (and how long until it clears)
- Worker/queue health
- Cache state
- Suggested next action (wait, enqueue, inspect enrichment logs, investigate repeated failures)

Cite any non-obvious findings with the exact file and line reference (e.g., `movies/projection_enrichment.py:NN`) so the user can jump in.
