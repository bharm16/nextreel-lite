# Scaling Runbook — nextreel-lite

Operational playbook for running nextreel-lite at 10× current traffic.
Complements `SYSTEM_DESIGN_REVIEW.md` with concrete ops-level controls.

## Quick knobs (no code change required)

| Env var | Default | 10× setting | Owner file |
|---|---|---|---|
| `POOL_MAX_SIZE` | `60` | `60`+ | `config/database.py` |
| `POOL_MIN_SIZE` | `5` | `10` | `config/database.py` |
| `TMDB_RATE_SEMAPHORE` | `200` | `200`–`400` | `movies/tmdb_client.py` |
| `REDIS_MAX_CONNECTIONS` | `30` | `60` | `app.py` |
| `LOCAL_ENRICHMENT_CONCURRENCY` | `20` | `40` | `movies/projection_enrichment.py` |
| `LOCAL_ENRICHMENT_MAX_PENDING` | `200` | `400` | `movies/projection_enrichment.py` |
| `SLOW_LOG_SAMPLE_RATE` | `1` | `10` | `app.py` (reduce log pressure) |
| `PROJECTION_ENRICHMENT_BLOCKS_RENDER` | `true` | `false` at 10× | `movies/projection_store.py` |
| `NAV_STATE_REDIS_READ_CACHE_ENABLED` | `false` | `true` after staging validation | `infra/navigation_state.py` |

### `PROJECTION_ENRICHMENT_BLOCKS_RENDER=false`

When set, `/movie/<tconst>` returns the core payload (title, year, ratings,
genre) immediately for projections in `core` / `stale` / `failed` state
instead of blocking on TMDb enrichment. Enrichment is enqueued for the next
visit. Trades first-view completeness for tail-latency insulation.

**When to enable:** `tmdb_api_duration_seconds` p99 > 5s sustained, or
`enrichment_timeout_total` rising.

### `NAV_STATE_REDIS_READ_CACHE_ENABLED=true`

Adds a Redis-backed read cache in front of `user_navigation_state`. Invalidated
on every `save_state`. Bounded safety window: optimistic-lock version check
still fires on conflict, so stale reads cannot corrupt state — they just cost
an extra retry.

**When to enable:** Only after validation in staging. `navigation_state_conflicts_total`
must NOT rise sharply after enabling. If it does, disable immediately.

## Prometheus alerts

### CRITICAL: Rate limiter degraded
```
- alert: RateLimiterMemoryFallback
  expr: rate_limiter_backend_info{backend="memory"} == 1
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: Rate limiter has fallen back to per-process memory store
    description: |
      The rate limiter cannot reach Redis and is using a per-process
      in-memory fallback. In multi-worker deployments the effective
      limit is now N × configured. Restore Redis connectivity.
      Look for `RATE LIMITER DEGRADED` in error logs.
```

### WARNING: Pool saturation
```
- alert: DatabasePoolWaitHigh
  expr: histogram_quantile(0.95, rate(pool_wait_time_seconds_bucket[5m])) > 0.5
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: Database pool checkout p95 > 500ms
    description: |
      Pool at `POOL_MAX_SIZE={{ $value }}` is saturating. Bump
      POOL_MAX_SIZE or investigate long-running queries.
      Dashboard: {{ .Values.grafana_url }}/d/db-pool
```

### WARNING: TMDb circuit breaker trips
```
- alert: TMDbCircuitBreakerOpen
  expr: tmdb_circuit_breaker_state == 2  # OPEN
  for: 30s
  labels:
    severity: warning
```

### WARNING: Enrichment backlog full
```
- alert: EnrichmentBacklogDrops
  expr: rate(enrichment_backlog_drop_total[5m]) > 0
  for: 1m
```

## CDN configuration

At 10× traffic, serving `/static/*` from the app server is wasteful CPU.
Put a CDN in front of the app with these rules:

| Path prefix | Cache TTL | Notes |
|---|---|---|
| `/static/css/output.css` | `1y`, `immutable` | Hashed via `CSS_VERSION` query param |
| `/static/js/*` | `1y`, `immutable` | Same pattern |
| `/static/img/*` | `30d` | Posters are user-independent |
| `/favicon.ico` | `7d` | |
| `/movie/tt*` (GET) | `60s`, `s-maxage=60` | Only if user is anonymous; `Vary: Cookie` |
| `/` (GET, anonymous) | `60s` | Same |

**Important:** Do NOT cache any POST routes (`/next_movie`, `/previous_movie`,
`/filtered_movie`, `/login`) or any path with `Set-Cookie: nr_sid=`.

Recommended providers:
- **bunny.net** — cheap, pull-through from origin, good for EU/US
- **CloudFront** — best if already on AWS
- **Cloudflare** — free tier, but watch for SSE/streaming incompatibilities

## Longer-term strategic items

### Read replicas
When `pool_wait_time_seconds` p95 stays > 500ms after pool bumps, split the
IMDb corpus (read-heavy, rarely written) from user state. Route these
through a read replica:

- `title.basics`, `title.ratings`, `movie_candidates`, `movie_projection` (SELECT only)
- `watched` (SELECT only)

Keep writes on the primary:
- `user_navigation_state`
- `movie_projection` (INSERT/UPDATE via enrichment)
- `users`, `watched` (INSERT)

### Multi-region deployment
For users outside the primary region, p99 will be dominated by
trans-continental round trips. Deploy read replicas in the target region
and route reads to the nearest. Writes still go to primary.

### Navigation state full write-through
The read cache (`NAV_STATE_REDIS_READ_CACHE_ENABLED`) is a stepping stone.
Full write-through would:

1. Write state to both Redis and MySQL in the same mutation.
2. Accept a Redis-first read with a periodic MySQL checkpoint.
3. Use a `nav_state:version:{session_id}` counter in Redis for optimistic
   locking instead of a MySQL column.

Estimated effort: 1 week + staged rollout.
Do this only after the read cache has been running clean for 2+ weeks.
