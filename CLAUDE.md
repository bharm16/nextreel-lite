# Nextreel-Lite

Async Python web app for personalized movie discovery. Quart (async Flask) + MySQL + Redis + TMDb API.

## Commands

```bash
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
```

## Architecture

```
app.py                  # Entry point — creates Quart app, wires dependencies
routes.py               # All HTTP endpoints (Blueprint "main")
movie_service.py        # MovieManager facade — coordinates navigation + rendering
movie_navigator.py      # Session-based prev/next stacks, queue management
movie_renderer.py       # Template rendering for movie detail pages
movies/
  movie.py              # Movie class — fetches and assembles movie data from TMDb + DB
  tmdb_client.py        # TMDbHelper — async HTTP client with circuit breaker
  query_builder.py      # SQL query builder for random movie fetching (MovieQueryBuilder)
  interfaces.py         # MovieFetcher protocol
session/
  keys.py               # Session key constants
  auth.py               # User registration in movie manager
  security.py           # Session fingerprinting, token rotation, security headers
infra/
  pool.py               # SecureConnectionPool + DatabaseConnectionPool wrapper
  cache.py              # Redis cache manager (namespaced, TTL-based)
  errors.py             # DatabaseError exception
  secrets.py            # Secret retrieval and validation
  metrics.py            # Prometheus metrics collector
  ssl.py                # SSL certificate validation
config/
  env.py                # get_environment() — single source for env detection
  session.py            # Session cookie and timeout defaults
  database.py           # DB connection config per environment
  api.py                # API secrets config
```

## Key Patterns

### Session State
- **Lightweight refs in session**: `CURRENT_MOVIE_KEY` stores only `{imdb_id, tmdb_id, title, slug}` (~500 bytes). Full movie data lives in Redis cache (`cache:movie:full:{tconst}`, 24h TTL).
- `MovieNavigator` reads/writes session directly via Quart's `session` proxy. All session keys are defined in `session/keys.py`.
- Session lifetime is managed by `EnhancedSessionSecurity` (8h max, 15min idle). `session/auth.py` handles only user registration.

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

- 28 test files in `tests/`, pytest-asyncio with `asyncio_mode = "auto"`
- `MovieManager.home()` returns a dict `{"default_backdrop_url": ...}` — not a rendered template
- `MovieManager.add_user()` calls `navigator.load_initial_queue()` (public method)
- Mock targets: `movie_service.MovieManager`, use `patch.dict(os.environ, {...})` for env vars (not module-level attribute patches)

## Gotchas

- **`logging_config.py`**: `setup_logging()` is NOT called at import time. `app.py` calls it explicitly. Importing `get_logger` alone is safe.
- **`_is_full_movie()`**: Checks for `"_full"` sentinel key, not for `"cast"` or `"plot"` keys. Full movie dicts have `"_full": True`.
- **Security headers**: Baseline headers (X-Frame-Options, nosniff, Permissions-Policy) apply in ALL environments. HSTS and CSP are production-only.
- **Rate limiting**: Applied to `/next_movie`, `/previous_movie`, `/filtered_movie`, and ops endpoints. Uses Redis with in-memory fallback.
- **`get_async_connection()`**: Raises `NotImplementedError`. Use `async with pool.acquire() as conn:` instead.
- **`.env` files**: Contain live credentials in git history. Hooks block Claude from editing them. Secrets must be rotated and managed via environment variables or a secrets manager.
