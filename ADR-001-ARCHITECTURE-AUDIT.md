# ADR-001: NextReel-Lite Full Architecture Audit

**Status:** Proposed
**Date:** 2026-03-22
**Deciders:** Engineering team
**Auditor:** Architecture review (automated)

---

## Context

NextReel-Lite is an async Python web application that serves random movie recommendations from an IMDb dataset, enriched with TMDb API data. The application runs on Quart (async Flask-like framework) with MySQL for persistence, Redis for sessions/caching, and Prometheus for observability.

This audit evaluates the full architecture across six dimensions: **session management, data access, security, performance, code organization, and observability**. Three prior audit documents exist in the repo (ARCHITECTURE_AUDIT.md, SYSTEM_DESIGN_REVIEW.md, TECH_DEBT_REPORT.md) — this ADR reconciles their findings with the current state of the codebase.

### Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Web framework | Quart (async) | 0.20.0 |
| ASGI server | Hypercorn | 0.17.3 |
| Python | CPython | 3.9.18 |
| Database | MySQL/MariaDB | via aiomysql 0.3.2 |
| Cache/Sessions | Redis | 7.1.1 (Upstash in prod) |
| External API | TMDb | via tmdbsimple 2.9.1 |
| Metrics | Prometheus | prometheus-client 0.21.1 |
| Encryption | Fernet + PBKDF2 | cryptography 46.0.5 |

---

## Architecture Overview

```
Browser (HTTP/HTTPS)
    │
    ▼
Quart App (app.py)
    ├── Middleware: correlation_id, security headers, metrics
    ├── Blueprint Routes (routes.py)
    │   ├── /health, /metrics, /ready        ← ops endpoints
    │   ├── /, /home                         ← content pages
    │   ├── /next_movie, /prev_movie         ← navigation
    │   ├── /set_filters, /filtered_movie    ← filtering
    │   └── /movie/<tconst>, /logout         ← display/auth
    │
    ├── MovieManager (movie_service.py)       ← Facade
    │   ├── MovieNavigator (movie_navigator.py)  ← session state
    │   └── MovieRenderer (movie_renderer.py)    ← template rendering
    │
    ├── Session Management
    │   ├── session_auth.py                  ← basic token + fingerprint
    │   ├── session_security_enhanced.py     ← encryption, rotation
    │   └── session_auth_enhanced.py         ← DEAD CODE
    │
    ├── Security & Caching
    │   ├── SecureConnectionPool             ← circuit breaker + rate limits
    │   ├── SecureCacheManager               ← HMAC + Fernet encrypted cache
    │   └── secrets_manager.py               ← pluggable secret backend
    │
    └── Data Access
        ├── DatabaseConnectionPool           ← aiomysql pool wrapper
        ├── DatabaseQueryExecutor            ← query execution
        └── filter_backend.py                ← dynamic SQL builder

Storage:
    ├── MySQL: IMDb dataset (title.basics, title.ratings, etc.)
    │          + denormalized cache tables (popular_movies_cache, recent_movies_cache)
    └── Redis: sessions (quart-session) + encrypted movie data cache
```

---

## Findings by Domain

### 1. Session Management — CRITICAL ISSUES

**Finding 1.1: Three competing session auth implementations**

The codebase contains three session auth modules that overlap in responsibility:

- `session_auth.py` (106 lines) — basic token generation + browser fingerprinting
- `session_security_enhanced.py` (711 lines) — Fernet encryption, key rotation, device fingerprinting, HTTPS enforcement
- `session_auth_enhanced.py` (11 lines) — dead code, only referenced in tests

Both `session_auth.init_session()` and `EnhancedSessionSecurity._before_request_handler()` execute on every request (app.py lines 117, 136, 183). This means session state is written twice with potentially conflicting logic.

**Severity:** Critical
**Impact:** Race conditions in session writes, confusing codebase, maintenance burden
**Recommendation:** Keep `session_security_enhanced.py` as the single source of truth. Merge any unique logic from `session_auth.py` into it. Delete `session_auth_enhanced.py`.

**Finding 1.2: Session bloat from full movie dicts**

MovieNavigator stores complete movie data dicts (5-10 KB each) in Redis sessions. With a prev_stack of 50 movies plus a queue, sessions can exceed 500 KB per user.

**Severity:** High
**Impact:** Redis memory pressure, slow session reads/writes, increased latency
**Recommendation:** Store only lightweight references (`{tconst, title, slug}`) in session. Fetch full data on render from cache or API.

---

### 2. Data Access — HIGH-PRIORITY ISSUES

**Finding 2.1: Silent error swallowing in DatabaseQueryExecutor**

`db_utils.py:78-84` catches all exceptions and returns `None`:

```python
try:
    return await self.db_pool.execute(query, params, fetch)
except Exception as e:
    logger.error("Query execution error: %s", e)
    return None  # Indistinguishable from "no results"
```

Callers in `filter_backend.py` and `movie.py` cannot distinguish "no data found" from "database is down." This masks outages and data corruption.

**Severity:** High
**Impact:** Silent failures in production, impossible to alert on database errors vs. empty results
**Recommendation:** Raise a custom `DatabaseError` exception. Let callers decide how to handle failures. Use `None` only for genuinely empty results.

**Finding 2.2: ORDER BY RAND() on non-cached queries**

`filter_backend.py` uses `ORDER BY RAND()` for filtered movie queries that don't hit the cache tables. This causes a full table scan + filesort, documented at 13+ seconds for post-2024 movies.

**Severity:** High
**Impact:** Request timeouts, database load spikes, poor UX
**Recommendation:** Use a pre-shuffled ID approach (`WHERE id >= RAND() * MAX(id)`) or maintain a randomized index column on filtered result sets.

**Finding 2.3: Cache tables never refreshed automatically**

The stored procedure `refresh_movie_caches()` populates `popular_movies_cache` and `recent_movies_cache`, but no scheduled job calls it. Cache staleness grows unbounded.

**Severity:** Medium
**Impact:** Stale recommendations, missing new movies
**Recommendation:** Add an APScheduler job (already in dependencies) to call the refresh procedure on a daily or weekly cadence.

**Finding 2.4: No foreign key constraints**

All table joins are INNER/LEFT without referential integrity enforcement. Orphan records in ratings/crew/principals tables are possible.

**Severity:** Low (IMDb data is externally managed)
**Impact:** Potential data inconsistency on manual updates
**Recommendation:** Add FK constraints or at minimum validate referential integrity during data imports.

---

### 3. Security — OVER-ENGINEERED FOR THREAT MODEL

**Finding 3.1: Security complexity disproportionate to threat model**

The application serves publicly available IMDb data. There are no user accounts, no PII, no payment data. Yet the security surface includes:

- HMAC + Fernet encrypted cache for public movie data (649 lines)
- Device fingerprinting with SHA3-256
- PBKDF2 with 600,000 iterations at startup
- Per-user and per-IP connection rate limiting (with no real user identity)
- psutil CPU sampling for token entropy (redundant — `secrets.token_urlsafe` is already cryptographically secure)
- SSL certificate validation module (377 lines)

Total security code: ~2,500 lines for an app with no sensitive data.

**Severity:** Medium (maintenance burden, not a vulnerability)
**Impact:** Increased complexity, slower startup, harder onboarding for new developers
**Recommendation:** Reduce to what the threat model requires — CSRF protection (keep), session integrity (keep but simplify), rate limiting (keep but use a standard library like `slowapi`). Remove HMAC cache signing, Fernet cache encryption, and CPU entropy sampling.

**Finding 3.2: X-Forwarded-For trusted without proxy validation**

`session_security_enhanced.py:245-247` reads `X-Forwarded-For` for client IP without validating that the request came from a trusted reverse proxy. An attacker can spoof their IP to bypass per-IP rate limits.

**Severity:** Medium
**Impact:** Rate limit bypass, incorrect fingerprinting
**Recommendation:** Configure a trusted proxy list and only accept forwarded headers from those IPs, or use Quart's `ProxyFix` middleware equivalent.

**Finding 3.3: Ops endpoints partially secured**

`/metrics` and `/ready` now require `OPS_AUTH_TOKEN` (Bearer token), which is an improvement over the prior state. However, the rate limiter for these endpoints is in-memory and won't work across multiple instances.

**Severity:** Low
**Impact:** Rate limit bypass in multi-instance deployments
**Recommendation:** Move rate limiting to Redis (already available) for distributed enforcement.

---

### 4. Performance — SIGNIFICANT BOTTLENECKS

**Finding 4.1: Movie data fetched twice per navigation**

When a user clicks "next movie":

1. `MovieNavigator._load_movies_into_queue()` calls `Movie.get_movie_data()` → 8 TMDb API calls
2. Redirect to `/movie/<tconst>` calls `MovieRenderer.render_movie_by_tconst()` → another 8 TMDb API calls

The data fetched in step 1 is stored in the session but not reused in step 2.

**Severity:** High
**Impact:** 2x API calls per navigation, doubled latency, wasted TMDb rate limit quota
**Recommendation:** Check `session[CURRENT_MOVIE_KEY]` before fetching. If data exists and matches the requested tconst, skip the second fetch.

**Finding 4.2: TMDbHelper instantiated per Movie object**

Every `Movie` object creates its own `TMDbHelper`, which creates a new `httpx.AsyncClient` with a 50-connection pool. A queue of 6 movies means 6 idle HTTP client pools consuming memory and file descriptors.

**Severity:** Medium
**Impact:** Resource leaks, unnecessary connection overhead
**Recommendation:** Share a single `TMDbHelper` instance (MovieManager already has one — pass it down).

**Finding 4.3: Request timeout defined but never enforced**

`routes.py:21` defines `_REQUEST_TIMEOUT = 30` but this value is never used in any `asyncio.wait_for()` or similar timeout wrapper. Long-running queries (see Finding 2.2) will block indefinitely.

**Severity:** Medium
**Impact:** Request hangs, thread starvation under load
**Recommendation:** Wrap async operations with `asyncio.wait_for(coro, timeout=_REQUEST_TIMEOUT)`.

---

### 5. Code Organization — CLEANUP NEEDED

**Finding 5.1: Dead code (~2,000+ lines)**

| File | Lines | Status |
|------|-------|--------|
| `session_auth_enhanced.py` | 11 | Dead — only in test imports |
| `backup_manager.py` | 405 | Orphaned — no callers |
| `setup_production_env.py` | 8 | Orphaned — no callers |
| `scripts/movie_queue.py` (OptimizedMovieQueue) | 508 | Unused class |
| `profiling/` directory | ~200 | Orphaned analysis scripts |
| Root-level test files (9 files) | ~350 | Duplicated/scattered |

**Severity:** Medium
**Impact:** Confusing codebase, false positive in searches, misleading test coverage
**Recommendation:** Delete dead files. Move root-level tests into `tests/`.

**Finding 5.2: Mixed logging patterns**

Three different logging approaches coexist:

- `get_logger(__name__)` — structured with correlation IDs (correct)
- `logging.getLogger(__name__)` — loses structured context
- Root logger usage in middleware.py
- f-string formatting in log calls (bypasses lazy evaluation)

**Severity:** Low
**Impact:** Inconsistent log output, missing correlation IDs in some modules
**Recommendation:** Standardize on `get_logger(__name__)` everywhere. Add a linting rule to catch raw `logging.getLogger` usage.

**Finding 5.3: Fragmented test suite**

Tests are split between `tests/` (8 files, ~734 lines) and root-level test files (9 files, ~350 lines). No `pytest.ini` or `pyproject.toml` test configuration. CI coverage target reportedly measures `tmdbsimple` instead of project code.

**Severity:** Medium
**Impact:** Unclear test coverage, CI not validating actual code
**Recommendation:** Consolidate all tests into `tests/`. Add `pyproject.toml` with `[tool.pytest.ini_options]` and correct coverage target.

---

### 6. Observability — SOLID FOUNDATION

**Finding 6.1: Correlation ID tracing (STRENGTH)**

Every request gets a UUID correlation ID via middleware, propagated through logs. This is well-implemented.

**Finding 6.2: Prometheus metrics integration (STRENGTH)**

MetricsCollector tracks movie recommendations, user sessions, user actions, and request latency. Grafana dashboard definition included.

**Finding 6.3: Loki integration available (STRENGTH)**

`python-logging-loki` configured for production log aggregation in Grafana.

**Finding 6.4: Missing distributed tracing**

No OpenTelemetry or similar distributed tracing. For a single-service app this is acceptable, but if the architecture grows, traces across TMDb API calls and database queries would be valuable.

**Severity:** Low (informational)

---

### 7. Configuration & Dependencies

**Finding 7.1: Python 3.9 approaching EOL**

Python 3.9 reached end-of-life in October 2025. The application is running on an unsupported Python version.

**Severity:** Medium
**Impact:** No security patches for Python runtime
**Recommendation:** Upgrade to Python 3.11 or 3.12 for continued security support and performance improvements.

**Finding 7.2: CI/CD targets wrong Python versions**

GitHub Actions reportedly tests against Python 3.6-3.8 while the app runs on 3.9. Tests may pass in CI but fail in production (or vice versa).

**Severity:** Medium
**Impact:** False confidence from CI, missed compatibility issues
**Recommendation:** Align CI matrix to Python 3.11+ (after upgrade).

**Finding 7.3: Stale hosting references**

`config/database.py:18` references `STACKHERO_DB_CONFIG` (old hosting provider). `local_env_setup.py` references a non-existent `UserAccounts` database. These are confusing for developers.

**Severity:** Low
**Recommendation:** Remove dead configuration references.

---

## Consequences

### What becomes easier if these findings are addressed

- **Debugging production issues** — eliminating silent error swallowing means failures surface immediately
- **Onboarding new developers** — removing 2,000+ lines of dead code and consolidating to one session auth system reduces cognitive load
- **Scaling** — fixing session bloat and double-fetching reduces Redis memory and TMDb API usage by ~50%
- **Maintaining security** — simplifying to threat-appropriate security reduces the surface area that needs auditing

### What becomes harder

- **Nothing significant** — these are cleanup and consolidation changes, not architectural pivots

### What we'll need to revisit

- If user accounts or PII are ever added, the simplified security posture will need to be re-evaluated
- If the app moves to multi-instance deployment, the in-memory rate limiters need Redis backing
- Python 3.11+ upgrade may require testing async library compatibility (aiomysql, Quart)

---

## Overall Scores

| Dimension | Score | Notes |
|-----------|-------|-------|
| Architectural maturity | 7/10 | Solid async foundation, good separation of concerns |
| Code quality | 6/10 | Dead code, mixed patterns, silent errors |
| Performance | 5/10 | Double-fetching, ORDER BY RAND(), no request timeouts |
| Security posture | 7/10 | Good intent, over-engineered for threat model |
| Observability | 8/10 | Correlation IDs, Prometheus, Grafana — well done |
| Production readiness | 6/10 | EOL Python, CI mismatch, silent failures |
| **Overall** | **6.5/10** | **Strong foundation with addressable issues** |
