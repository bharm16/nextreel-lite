# Nextreel-Lite

Async Python web app for personalized movie discovery. Quart (async Flask) + MySQL + Redis + TMDb API.

## Commands

```bash
# One-time local setup (venv, pip, npm, Tailwind build; copies .env.example → .env if missing)
python3 scripts/bootstrap_dev.py
# or: npm run bootstrap

# Dev server
python3 app.py                          # Starts on http://127.0.0.1:5000

# Tests
python3 -m pytest tests/ -v             # All tests
python3 -m pytest tests/test_app.py -v  # Single file
python3 -m pytest tests/ --cov=. --cov-report=term-missing  # With coverage

# Lint & format
black . --line-length 100               # Format (configured in pyproject.toml)
flake8 . --exclude=venv,node_modules    # Lint
mypy . --ignore-missing-imports         # Type check

# Tailwind CSS
npm run build-css                       # Rebuild CSS from Tailwind

# Cache refresh (manual)
mysql -e "CALL refresh_movie_caches()"  # Rebuild denormalized cache tables

# Background worker
arq worker.WorkerSettings              # Start arq worker for enrichment/refresh jobs
arq worker.MaintenanceWorkerSettings   # Optional: second worker for heavy maintenance (isolates refresh_movie_candidates from enrichment)
```

## Architecture

```
app.py                  # Entry point — delegates to nextreel.web.app
worker.py               # arq CLI entry point — delegates to nextreel.workers.worker
settings.py             # Unified Config class (composes config/ sub-modules)
env_bootstrap.py        # Minimal env detection helpers (no package side effects)
logging_config.py       # setup_logging() — called by app.py, NOT at import time
nextreel/
  application/
    movie_service.py    # MovieManager facade — coordinates navigation + rendering
    movie_navigator.py  # Prev/next stacks, queue management
    auth_flows.py       # OAuth and registration orchestration services
    navigation_state_service.py  # NavigationStateStore — mutate/load/bind state
    home_prewarm_service.py      # Background queue prewarm on home load
    letterboxd_import_service.py # Letterboxd CSV import orchestration
    watched_progress_service.py  # Enrichment progress tracking
  domain/
    filter_contracts.py    # FilterState / MovieCriteria type contracts
    navigation_state.py    # NavigationState dataclass, MutationResult, constants
  web/
    app.py              # create_app() — Quart factory, wires dependencies
    middleware.py        # Correlation ID tracking middleware
    movie_renderer.py    # Template rendering for movie detail pages
    route_services.py    # Route-level presenters and mutation services
    request_context.py   # Per-request state setup (g.state, g.services)
    lifecycle.py         # before_serving / after_serving hooks
    routes/
      shared.py          # Blueprint, NextReelServices, shared helpers
      auth.py            # Login, register, OAuth routes
      movies.py          # Home, movie detail routes
      navigation.py      # Next/previous/filtered movie routes
      watched.py         # Watched list routes
      ops.py             # Health, readiness, metrics routes
  bootstrap/
    movie_manager_factory.py  # Composition root for MovieManager
  workers/
    worker.py            # arq WorkerSettings, job definitions
movies/
  movie.py              # Movie class — fetches and assembles movie data from TMDb + DB
  tmdb_client.py        # TMDbHelper — async HTTP client with circuit breaker
  tmdb_parser.py        # TMDb response parsing
  tmdb_metrics.py       # Prometheus emission for TMDb transport outcomes
  query_builder.py      # Genre-clause SQL helper (MovieQueryBuilder)
  search_queries.py     # Title-search SQL for /api/search (navbar Spotlight)
  candidate_store.py    # Data access layer for movie candidates
  candidate_filter_pool_cache.py  # Per-filter candidate pool cache
  movie_payload.py      # Movie payload assembly helpers
  projection_store.py   # Projection manager — read-path + enrichment orchestration facade (contains ProjectionReadService)
  projection_repository.py  # Projection SQL persistence + payload shaping helpers
  projection_enrichment.py   # Enrichment coordinator + TMDb fetch service + payload differ
  projection_state.py   # State enum, policy constants, EnrichmentResult
  watched_store.py      # Watched-list persistence
  letterboxd_import.py  # Letterboxd CSV parsing and title matching
  filter_parser.py      # Filter query parsing and validation
session/
  keys.py               # Session key constants
  user_auth.py          # Registration/authentication helpers
  user_preferences.py   # User preference persistence (exclude-watched default)
  quart_session_compat.py  # Compatibility shim for quart-session 3.0.0
infra/
  pool.py               # SecureConnectionPool + DatabaseConnectionPool wrapper
  pool_monitors.py      # Pool health/metrics monitors
  cache.py              # Redis cache manager (namespaced, TTL-based)
  errors.py             # DatabaseError exception
  secrets.py            # Secret retrieval and validation
  metrics.py            # Prometheus metrics collector
  metrics_groups.py     # Metric grouping/labels helpers
  worker_metrics.py     # arq worker metrics
  ssl.py                # SSL certificate validation
  security_headers.py   # Baseline + production-only HTTP security headers
  rate_limit.py         # Redis-backed rate limiter with in-memory fallback
  client_ip.py          # Client IP extraction utilities
  navigation_state_repository.py  # Navigation state SQL persistence
  runtime_schema.py     # Runtime schema creation and validation
  ops_auth.py           # Authentication for ops/admin endpoints
  route_helpers.py      # Shared route utilities (csrf, rate limit, timeout decorators)
  filter_normalizer.py  # Filter input normalization and validation
  integrity_checks.py   # Data integrity validators
  legacy_migration.py   # One-shot legacy data migrations
  maintenance_jobs.py   # Worker job bodies (refresh candidates, purge state)
  job_queue.py          # arq job queue installation helpers
  redis_runtime.py      # Redis connection setup
  time_utils.py         # Time/timezone helpers
config/
  env.py                # get_environment() — single source for env detection
  session.py            # Session cookie and timeout defaults
  database.py           # DB connection config per environment
  api.py                # API secrets config
scripts/                # One-off and maintenance scripts
ops/                    # Deployment and operational tooling
docs/                   # Architecture and design documentation
```

## Environment

Required (app fails to start without these):
- `TMDB_API_KEY` — TMDb API bearer token (validated by `infra/secrets.py` on startup)
- `FLASK_SECRET_KEY` — Session signing key
- `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` — MySQL connection (production uses `PROD_DB_*` with fallback)

Optional:
- `REDIS_URL` — Redis connection (required for worker, rate limiting, caching)
- `OPS_AUTH_TOKEN` — Auth for `/ready` and `/metrics` endpoints (unauthenticated in dev)
- `TRUSTED_PROXIES` — Comma-separated IPs for `X-Forwarded-For` trust (rate limiting)
- `GRAFANA_LOKI_KEY` — Enables Grafana Loki log shipping
- `NAV_STATE_DUAL_WRITE_ENABLED` — Navigation migration dual-write (default: `true`). **Ops: flip to `false` once the 7-day migration window has elapsed** to remove write-amplification on navigation mutations.

## Key Patterns

### Shared helpers (prefer these over hand-rolling)

- **Env parsing**: `infra/time_utils.py` provides `env_bool(name, default)`, `env_int(name, default)`, `env_float(name, default)` — all swallow invalid values and fall back to the default. Never hand-roll `int(os.getenv(...))` — use `env_int`.
- **Environment detection**: `config/env.py` re-exports `get_environment()` from `env_bootstrap`. Never inline `os.getenv("NEXTREEL_ENV")`.
- **Current year**: `infra/time_utils.current_year()` — 1-hour TTL cached, avoids recomputing on every query build.
- **Cache single-flight miss path**: `SimpleCacheManager.safe_get_or_set(namespace, key, loader, ttl)` in `infra/cache.py` — "try cache, fall back to loader, swallow cache errors" wrapper. Does NOT provide coalescing; use `get_or_load` for that.
- **Bounded TTL cache**: `infra.cache.LruExpiringMap(max_keys, ttl_seconds)` — LRU + per-entry TTL. Used by rate-limit in-memory fallback and metrics active-user tracking.
- **SSL context for MySQL**: `infra/ssl.build_mysql_ssl_context(cert_path)` — stateless module-level helper with CERT_REQUIRED, check_hostname=False (documented exception for IP-based MySQL certs), TLSv1.2+ minimum, and the reviewed cipher string. `SecureConnectionPool` delegates to this.
- **Metric emission**: `infra.metrics_groups.safe_emit(fn, *args, **kwargs)` — call a metric emit function, swallow and debug-log any exception. Use this instead of hand-rolled `try: metric.inc(); except Exception: pass`.
- **Runtime schema helpers**: `infra.runtime_schema._ensure_index(pool, table, name, create_sql)` and `_ensure_column(...)` — run CREATE/ALTER directly and catch the MySQL duplicate-key (1061) / duplicate-column (1060) errnos as idempotent. Eliminates TOCTOU SELECT probes.

### Session State
- Navigation state is **MySQL-backed** (`user_navigation_state` table) via `NavigationStateStore` in `nextreel/application/navigation_state_service.py`. Uses optimistic locking (version column, 5 retries on conflict with exponential full-jitter backoff).
- Full movie data lives in Redis cache (`cache:movie:full:{tconst}`, 24h TTL). Session stores lightweight refs only.
- Session cookie lifetime: defaults from `config/session.py` (`SESSION_IDLE_TIMEOUT_MINUTES`, `MAX_SESSION_DURATION_HOURS`, default 15min/8h). User authentication helpers live in `session/user_auth.py` (`register_user`, `authenticate_user`, `hash_password_async`, `find_or_create_oauth_user`).
- **Migration period**: Dual-write from Redis session → MySQL is enabled by default for 7 days (`NAV_STATE_DUAL_WRITE_ENABLED`, `NAV_STATE_MIGRATION_MIN_DAYS`). The dual-write probe is cached in-process for 60s to avoid per-request DB round-trips.

### Navigation Routes
- `/next_movie` and `/previous_movie` are **POST-only** with CSRF tokens. All "Pick a Movie" buttons in templates use `<form method="POST">` with hidden `csrf_token` field.
- `/filters` (not `/setFilters`) is the filter page. GET to view, POST to `/filtered_movie` to apply.

### Environment Detection
Always use `from config.env import get_environment` — never inline `os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", ...))`.

### Logging
Use `%s`-style lazy formatting, never f-strings:
```python
logger.info("Fetched %d movies in %.2fs", count, elapsed)  # correct
logger.info(f"Fetched {count} movies")                      # wrong
```

### SQL
All queries must use parameterized placeholders (`%s`), including LIMIT and OFFSET. Never use f-string interpolation for SQL values. The `MovieQueryBuilder` class in `movies/query_builder.py` has static methods for building queries.

### TMDb API
- API key is sent via `Authorization: Bearer` header, not query params.
- Credits are fetched once per movie; cast info is derived from the same response (not a separate call).
- Circuit breaker (`_CircuitBreaker`) uses async locks — all methods are `async def`.

### SSL / Database
- `ssl.CERT_REQUIRED` always — never `CERT_NONE`. `check_hostname=False` is intentional (MySQL uses IP-based certs).
- Connection pool circuit breaker mutations are protected by `_cb_lock` (asyncio.Lock).

## Testing

- pytest-asyncio with `asyncio_mode = "auto"` in `tests/`
- `MovieManager.home()` returns a dict `{"default_backdrop_url": ...}` — not a rendered template
- Navigator queue is primed via `prewarm_queue()` (called from `home_prewarm_service`).
- Mock targets: `movie_service.MovieManager`, use `patch.dict(os.environ, {...})` for env vars (not module-level attribute patches)

## Gotchas

- **`logging_config.py`**: `setup_logging()` is NOT called at import time. `app.py` calls it explicitly. Importing `get_logger` alone is safe.
- **`_is_full_movie()`**: Checks for `"_full"` sentinel key, not for `"cast"` or `"plot"` keys. Full movie dicts have `"_full": True`.
- **Security headers**: Baseline headers (X-Frame-Options, nosniff, Permissions-Policy) apply in ALL environments. HSTS and CSP are production-only.
- **Rate limiting**: Applied to `/next_movie`, `/previous_movie`, `/filtered_movie`, and ops endpoints. Uses Redis with in-memory fallback.
- **`get_async_connection()`**: Raises `NotImplementedError`. Use `async with pool.acquire() as conn:` instead.
- **`.env` files**: Contain live credentials in git history. Hooks block Claude from editing them. Secrets must be rotated and managed via environment variables or a secrets manager.
- **Runtime tables**: `ensure_runtime_schema()` creates `runtime_metadata`, `user_navigation_state`, `movie_projection`, and `movie_candidates` on startup (`IF NOT EXISTS`). Don't create these manually.
- **Runtime-created indexes**: `infra/runtime_schema.py` adds indexes at startup that aren't in the base CREATE TABLE definitions. The authoritative list is `_RUNTIME_REPAIR_HELPER_NAMES` in that file. Current helpers include `idx_movie_candidates_refreshed_at`, `idx_movie_candidates_shuffle` (supports the hot candidate-fetch ORDER BY at `movies/candidate_store.py:147`), `idx_movie_candidates_bucket_filter`, `idx_movie_candidates_primaryTitle` (128-byte prefix index supporting `/api/search` title lookup via `movies/search_queries.build_search_query`), and the `movie_candidates` FULLTEXT genres index. When checking indexes manually, don't rely solely on `_RUNTIME_SCHEMA_STATEMENTS` — the repair helpers run alongside it.
- **Projection states**: `core` (minimal IMDb data) → `ready` (TMDb-enriched) → `stale` (>7 days) → `failed` (enrichment error). Enrichment is async-enqueued via `enrich_projection` worker job with 15-min cooldown.
- **CI security gates**: TruffleHog blocks the build on verified secrets. Bandit and pip-audit run but are warnings only (`|| true`). Tests require 40% coverage on Python 3.11 and 3.12.
- **`.claude.local.md`**: Not in `.gitignore` — add it if you use local Claude overrides to avoid committing personal preferences.
- **Runtime schema backfills are gated**: `ensure_movie_candidates_shuffle_key` fires its `UPDATE ... WHERE shuffle_key IS NULL` and `ALTER TABLE ... MODIFY COLUMN shuffle_key INT NOT NULL` **once**, then records `shuffle_key_backfill_done` in `runtime_metadata`. If you need to re-run the backfill (e.g. after a data reload), delete that metadata row. The `_ensure_column` call itself is always idempotent.
- **Dual-write flag is cached**: `legacy_migration.dual_write_enabled()` caches its result in-process for 60 seconds (`_DUAL_WRITE_CACHE_TTL_SECONDS`). If you flip `NAV_STATE_DUAL_WRITE_ENABLED` via env, it takes up to 60s to propagate. Tests can call `_reset_dual_write_cache()` to force a fresh read.
- **New shared helpers**: See the "Shared helpers" subsection under "Key Patterns" for env parsing (`env_bool`/`env_int`/`env_float`), `safe_get_or_set`, `LruExpiringMap`, `build_mysql_ssl_context`, `safe_emit`, and the runtime-schema `_ensure_index`/`_ensure_column` helpers. Prefer these over hand-rolling equivalents.
