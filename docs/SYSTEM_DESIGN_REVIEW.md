# NextReel-Lite System Design Review

**Date:** 2026-03-20
**Scope:** Full architecture analysis with actionable improvement recommendations

---

## 1. Current Architecture Overview

NextReel-Lite is an async Python web app (Quart) that serves random movie recommendations. Users navigate movies via next/previous controls, apply filters, and view movie details sourced from a local IMDb MySQL database + TMDb API.

### Component Diagram

```
                    ┌─────────────────────────────────────┐
                    │            Quart App (app.py)        │
                    │  ┌──────────┐  ┌──────────────────┐ │
                    │  │ Blueprint │  │ Middleware        │ │
  Browser ──────────┤  │ (routes) │  │ (correlation_id, │ │
                    │  │          │  │  security headers,│ │
                    │  │          │  │  metrics)         │ │
                    │  └────┬─────┘  └──────────────────┘ │
                    └───────┼─────────────────────────────┘
                            │
                   ┌────────▼────────┐
                   │  MovieManager   │ ← Facade
                   │  (movie_service)│
                   └───┬────────┬───┘
              ┌────────▼──┐  ┌──▼───────────┐
              │ Navigator │  │  Renderer    │
              │ (stacks,  │  │  (templates, │
              │  queue)   │  │   Movie obj) │
              └─────┬─────┘  └──────┬───────┘
                    │               │
         ┌──────────▼───┐    ┌──────▼──────┐
         │ FilterBackend│    │ TMDbHelper  │
         │ (SQL queries)│    │ (HTTP/httpx)│
         └──────┬───────┘    └─────────────┘
                │
      ┌─────────▼──────────┐
      │ DatabaseConnection │
      │ Pool → SecurePool  │
      │ (aiomysql + circuit│
      │  breaker + rate    │
      │  limiting)         │
      └─────────┬──────────┘
                │
         ┌──────▼──────┐       ┌────────────┐
         │   MySQL     │       │   Redis    │
         │  (IMDb DB)  │       │ (sessions, │
         │             │       │  cache)    │
         └─────────────┘       └────────────┘
```

### Data Flow: "Next Movie" Request

1. `POST /next_movie` → `routes.py`
2. `MovieManager.next_movie()` → delegates to `MovieNavigator`
3. Navigator checks session stacks (future → queue → fetch new)
4. If fetch needed: `ImdbRandomMovieFetcher.fetch_random_movies()` → SQL to MySQL
5. For each row: `Movie(tconst).get_movie_data()` → 8 parallel TMDb API calls
6. Language filter applied, queue populated, redirect to `/movie/<tconst>`
7. `MovieRenderer.render_movie_by_tconst()` re-fetches movie data and renders template

---

## 2. Critical Issues

### 2.1 Movie Data Fetched Twice Per Navigation

**Severity: High (performance)**

When a user hits "next movie," the flow is:

1. `MovieNavigator._load_movies_into_queue()` calls `Movie(tconst).get_movie_data()` for each candidate — making 8+ TMDb API calls per movie.
2. The movie data dict is stored in the session queue.
3. `next_movie()` redirects to `/movie/<tconst>`.
4. `MovieRenderer.render_movie_by_tconst()` creates a **new** `Movie(tconst)` and calls `get_movie_data()` **again** — another 8 TMDb API calls.

**Impact:** Every movie view costs ~16 TMDb API calls instead of ~8. At scale this doubles your rate limit consumption and doubles latency.

**Fix:** `render_movie_by_tconst` should pull movie data from the session's `CURRENT_MOVIE_KEY` first, falling back to a fresh fetch only if missing. The data is already there — it was just fetched 200ms ago.

### 2.2 TMDbHelper Instantiated Per Movie Object

**Severity: High (resource leak)**

Every `Movie(tconst, db_pool)` creates its own `TMDbHelper()`, which creates its own `httpx.AsyncClient` with a 50-connection pool. If you fetch 6 movies in a queue fill, that's 6 separate HTTP client pools opened and never explicitly closed (the `Movie.close()` method exists but is never called).

**Fix:** Pass the `TMDbHelper` instance from `MovieManager` into `Movie` objects, or make it a module-level singleton. One `httpx.AsyncClient` is sufficient for the entire application.

### 2.3 Full Movie Dicts Stored in Session

**Severity: Medium (scalability)**

Each movie data dict includes cast arrays, image URLs, credits, watch providers — easily 5-10 KB per movie. With `prev_stack` (50 movies), `future_stack`, `watch_queue`, and `current_movie`, a single session can balloon to 500 KB+. This is stored in Redis on every request.

**Fix:** Store only `{tconst, tmdb_id, title, slug}` in session stacks. Fetch full data on render (using the cache system you already built but aren't using for this).

### 2.4 Fire-and-Forget Task in Request Handler

**Severity: Medium (silent failures)**

```python
# movie_service.py:79
asyncio.create_task(self._ensure_queue())
```

In `home()`, this task runs detached. If it raises an exception, it's silently swallowed (no `try/except`, no `add_done_callback`). Quart will log "Task exception was never retrieved" but the user gets no feedback.

**Fix:** Either `await` it, or attach an error callback:
```python
task = asyncio.create_task(self._ensure_queue())
task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
```

### 2.5 Three Separate Redis Connections

**Severity: Medium (operational complexity)**

`app.py` creates three independent Redis connections:
1. `setup_redis()` — for quart-session
2. `startup()` — `app.redis_client` via `init_redis_pool()`
3. `SecureCacheManager` — its own connection pool

These have different configurations and aren't coordinated. The `app.redis_client` from `startup()` doesn't appear to be used anywhere except warm-up.

**Fix:** Create a single Redis connection pool and share it across session storage, caching, and any other Redis needs.

---

## 3. Architectural Concerns

### 3.1 Over-Engineered Security Layer

The security infrastructure is ~2,000 lines across `secure_pool.py`, `secure_cache.py`, and `session_security_enhanced.py`. For a movie recommendation app with no user accounts, no PII, and no payment processing, this is disproportionate:

- **Per-user connection rate limiting** in the DB pool — but there are no real "users" (just anonymous session UUIDs)
- **Per-IP connection limits** — but users share IPs behind NAT/proxies
- **HMAC-signed + Fernet-encrypted cache entries** — for public movie data
- **Device fingerprinting with SHA3-256** — for sessions that contain movie preferences
- **PBKDF2 with 600,000 iterations** at startup — adds latency for key derivation
- **psutil CPU sampling** for token entropy — `secrets.token_urlsafe(32)` is already cryptographically secure

This isn't inherently wrong, but it creates real costs: startup latency, per-request overhead, operational complexity, and a larger attack surface from the security code itself. For this app's threat model, standard Quart session management + Redis + HTTPS would be sufficient.

### 3.2 No API Layer / No Separation of Concerns in Data Fetching

`Movie.get_movie_data()` is doing too much: it fetches from the DB, calls 8 TMDb endpoints, formats data for templates, and attempts caching — all in one 80-line method. There's no service layer between "get raw data" and "format for display."

This makes it impossible to:
- Cache TMDb responses independently (e.g., credits change less often than watch providers)
- Return JSON for a future API endpoint
- Test data fetching without TMDb

**Recommendation:** Extract a `MovieDataService` that handles fetching + caching raw data, and let `Movie` (or the renderer) handle presentation formatting.

### 3.3 Session as Primary Data Store

The entire navigation state (stacks, queue, seen list) lives in the server-side session. This means:
- Every request serializes/deserializes potentially large session data to/from Redis
- There's no way to analyze user behavior (what movies are popular, what filters are common)
- If Redis restarts, all user state is lost
- No way to resume across devices

For now this works, but if you want analytics or multi-device support, you'll need a user state table in MySQL.

### 3.4 `DatabaseQueryExecutor` Is Redundant

`db_utils.py` defines `DatabaseQueryExecutor` which wraps `DatabaseConnectionPool.execute()`. But `DatabaseConnectionPool` already wraps `SecureConnectionPool.execute_secure()`. So the call chain is:

```
DatabaseQueryExecutor.execute_async_query()
  → DatabaseConnectionPool.execute()
    → SecureConnectionPool.execute_secure()
      → aiomysql cursor
```

Three layers of abstraction for a single query. The legacy `execute_async_query_legacy` method is also still present and appears unused. Collapse this to one layer.

### 3.5 `MovieManager.start()` Called Twice

In `app.py`, `movie_manager.start()` is called in both the `lifespan` context manager AND the `startup` before_serving hook. The lifespan calls `start()`, then yields; separately, `startup()` also calls `start()`, then does warm-up queries. This means `db_pool.init_pool()` runs twice (the second call may be a no-op depending on aiomysql's behavior, but it's confusing and fragile).

**Fix:** Consolidate startup into either the lifespan OR the before_serving hook, not both.

---

## 4. Specific Improvement Recommendations

### Priority 1: Fix the Double-Fetch (Biggest Performance Win)

In `movie_navigator.py`, `next_movie()` already stores the full movie data in `session[CURRENT_MOVIE_KEY]`. Update `render_movie_by_tconst` to check for this:

```python
async def render_movie_by_tconst(self, user_id, tconst, template_name="movie.html"):
    # Check if we already have this movie's data in session
    current = session.get(CURRENT_MOVIE_KEY)
    if current and current.get("imdb_id") == tconst:
        return await render_template(template_name, movie=current)

    # Fallback to fresh fetch
    movie_instance = Movie(tconst, self.db_pool)
    movie_data = await movie_instance.get_movie_data()
    ...
```

### Priority 2: Share TMDbHelper Instance

Pass it from MovieManager → MovieNavigator → Movie:

```python
# In Movie.__init__
def __init__(self, tconst, db_pool, tmdb_helper=None):
    self.tmdb_helper = tmdb_helper or TMDbHelper()
```

### Priority 3: Slim Down Session Data

Store only identifiers in navigation stacks. Use `SecureCacheManager` (which already exists and is initialized) to cache full movie data by tconst.

### Priority 4: Consolidate Redis Connections

Create one Redis pool in app startup and pass it to all consumers.

### Priority 5: Consolidate Startup Logic

Remove the duplicate `movie_manager.start()` call. Pick one lifecycle mechanism.

---

## 5. What's Working Well

- **Parallel TMDb fetching** in `Movie.get_movie_data()` with `asyncio.gather` is the right pattern
- **Circuit breaker** in the connection pool is solid production practice
- **Correlation IDs** for request tracing are properly implemented
- **Prometheus metrics** integration is comprehensive
- **Query optimization** with cache tables and fulltext fallback shows good DB performance awareness
- **Blueprint-based routing** with dependency injection via `init_routes()` is clean
- **Session key centralization** in `session_keys.py` prevents typo bugs

---

## 6. Trade-off Summary

| Decision | Current | Recommended | Trade-off |
|----------|---------|-------------|-----------|
| Movie data storage | Full dicts in session | IDs in session + Redis cache | Adds a cache lookup per render, saves ~10x session bandwidth |
| TMDb client | Per-Movie instance | Shared singleton | Requires passing through constructors, eliminates connection pool waste |
| Security layer | Enterprise-grade | Standard Quart + HTTPS | Less defense-in-depth, massive reduction in complexity and latency |
| Redis connections | 3 separate | 1 shared pool | Requires refactoring app.py startup, simpler ops |
| DB query layers | 3 wrappers | 1 (pool + execute) | Breaking change for code using `DatabaseQueryExecutor`, cleaner API |

---

## 7. What to Revisit as the System Grows

- **If adding user accounts:** Move session state to a users table, add proper auth
- **If traffic exceeds TMDb rate limits:** Add a dedicated TMDb response cache (the `SecureCacheManager` is already built for this — just wire it in)
- **If adding search/browse:** You'll need proper pagination and likely Elasticsearch
- **If going multi-instance:** The fire-and-forget `create_task` calls need to be idempotent; consider a task queue (Celery/Dramatiq)
