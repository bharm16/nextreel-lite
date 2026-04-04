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
```

## Architecture

```
app.py                  # Entry point — creates Quart app, wires dependencies
routes.py               # All HTTP endpoints (Blueprint "main")
movie_service.py        # MovieManager facade — coordinates navigation + rendering
movie_navigator.py      # Prev/next stacks, queue management (backed by NavigationStateStore)
movie_renderer.py       # Template rendering for movie detail pages
logging_config.py       # setup_logging() — called by app.py, NOT at import time
middleware.py            # Correlation ID tracking middleware
settings.py             # Unified Config class
worker.py               # arq background worker (Redis-backed enrichment/refresh jobs)
movies/
  movie.py              # Movie class — fetches and assembles movie data from TMDb + DB
  tmdb_client.py        # TMDbHelper — async HTTP client with circuit breaker
  query_builder.py      # SQL query builder for random movie fetching (MovieQueryBuilder)
  interfaces.py         # MovieFetcher protocol
  candidate_store.py    # Data access layer for movie candidates
  projection_store.py   # Data access layer for movie projections
  filter_parser.py      # Filter query parsing and validation
session/
  keys.py               # Session key constants
  auth.py               # User registration in movie manager
  security.py           # Session fingerprinting, token rotation, security headers
  quart_session_compat.py  # Compatibility shim for quart-session 3.0.0
infra/
  pool.py               # SecureConnectionPool + DatabaseConnectionPool wrapper
  cache.py              # Redis cache manager (namespaced, TTL-based)
  errors.py             # DatabaseError exception
  secrets.py            # Secret retrieval and validation
  metrics.py            # Prometheus metrics collector
  ssl.py                # SSL certificate validation
  security_headers.py   # Baseline + production-only HTTP security headers
  rate_limit.py         # Redis-backed rate limiter with in-memory fallback
  client_ip.py          # Client IP extraction utilities
  navigation_state.py   # DB-backed navigation state (NavigationStateStore)
  runtime_schema.py     # Runtime schema creation and validation
  ops_auth.py           # Authentication for ops/admin endpoints
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
- `NAV_STATE_DUAL_WRITE_ENABLED` — Navigation migration dual-write (default: `true`)

## Key Patterns

### Session State
- Navigation state is **MySQL-backed** (`user_navigation_state` table) via `NavigationStateStore` in `infra/navigation_state.py`. Uses optimistic locking (version column, 2 retries on conflict).
- Full movie data lives in Redis cache (`cache:movie:full:{tconst}`, 24h TTL). Session stores lightweight refs only.
- Session lifetime: 8h max, 15min idle (`EnhancedSessionSecurity`). `session/auth.py` handles only user registration.
- **Migration period**: Dual-write from Redis session → MySQL is enabled by default for 7 days (`NAV_STATE_DUAL_WRITE_ENABLED`, `NAV_STATE_MIGRATION_MIN_DAYS`).

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
- `MovieManager.add_user()` is a backward-compatible no-op. Navigator queue is primed via `prewarm_queue()`.
- Mock targets: `movie_service.MovieManager`, use `patch.dict(os.environ, {...})` for env vars (not module-level attribute patches)

## Gotchas

- **`logging_config.py`**: `setup_logging()` is NOT called at import time. `app.py` calls it explicitly. Importing `get_logger` alone is safe.
- **`_is_full_movie()`**: Checks for `"_full"` sentinel key, not for `"cast"` or `"plot"` keys. Full movie dicts have `"_full": True`.
- **Security headers**: Baseline headers (X-Frame-Options, nosniff, Permissions-Policy) apply in ALL environments. HSTS and CSP are production-only.
- **Rate limiting**: Applied to `/next_movie`, `/previous_movie`, `/filtered_movie`, and ops endpoints. Uses Redis with in-memory fallback.
- **`get_async_connection()`**: Raises `NotImplementedError`. Use `async with pool.acquire() as conn:` instead.
- **`.env` files**: Contain live credentials in git history. Hooks block Claude from editing them. Secrets must be rotated and managed via environment variables or a secrets manager.
- **Runtime tables**: `ensure_runtime_schema()` creates `runtime_metadata`, `user_navigation_state`, `movie_projection`, and `movie_candidates` on startup (`IF NOT EXISTS`). Don't create these manually.
- **Projection states**: `core` (minimal IMDb data) → `ready` (TMDb-enriched) → `stale` (>7 days) → `failed` (enrichment error). Enrichment is async-enqueued via `enrich_projection` worker job with 15-min cooldown.
- **CI security gates**: TruffleHog blocks the build on verified secrets. Bandit and pip-audit run but are warnings only (`|| true`). Tests require 40% coverage on Python 3.11 and 3.12.
- **`.claude.local.md`**: Not in `.gitignore` — add it if you use local Claude overrides to avoid committing personal preferences.
