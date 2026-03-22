# NextReel-Lite Architecture Action Plan

**Date:** 2026-03-22
**Reference:** ADR-001-ARCHITECTURE-AUDIT.md

---

## Scoring Key

- **Impact:** How much this improves reliability, performance, or maintainability (1-5)
- **Effort:** Engineering time required (1 = hours, 2 = 1-2 days, 3 = 3-5 days, 4 = 1-2 weeks, 5 = 2+ weeks)
- **Priority:** Impact ÷ Effort ratio, then judgment-adjusted

---

## Sprint 1 — Critical Fixes (Do Now)

| # | Action | Impact | Effort | Files Affected |
|---|--------|--------|--------|----------------|
| 1 | **Fix silent error swallowing in DatabaseQueryExecutor** | 5 | 1 | `db_utils.py`, `filter_backend.py`, `scripts/movie.py` |
| 2 | **Consolidate session auth to one implementation** | 4 | 2 | `session_auth.py`, `session_security_enhanced.py`, `session_auth_enhanced.py`, `app.py` |
| 3 | **Eliminate double movie data fetching** | 5 | 2 | `movie_navigator.py`, `movie_renderer.py`, `routes.py` |
| 4 | **Delete dead code** | 3 | 1 | `session_auth_enhanced.py`, `backup_manager.py`, `setup_production_env.py`, `profiling/` |

### Details

**Action 1: Fix silent error swallowing**
- Create a `DatabaseError` exception class
- Change `db_utils.py:78-84` to raise `DatabaseError` instead of returning `None`
- Update callers in `filter_backend.py` and `scripts/movie.py` to catch `DatabaseError` and handle appropriately (return error response, fallback to cache, etc.)
- Add a `/health` check that distinguishes "healthy" from "degraded (DB unreachable)"

**Action 2: Consolidate session auth**
- Delete `session_auth_enhanced.py` (dead code, 11 lines)
- Audit `session_auth.py` for any logic not present in `session_security_enhanced.py` (likely: `init_session()` token generation, `max_year` dynamic calculation)
- Merge unique logic into `session_security_enhanced.py`
- Update `app.py` to only call one session handler in `before_request`
- Update test imports

**Action 3: Eliminate double fetching**
- In `MovieRenderer.render_movie_by_tconst()`, check `session[CURRENT_MOVIE_KEY]` first
- If `tconst` matches, use cached movie data directly
- Only call `Movie.get_movie_data()` if cache miss
- Expected result: 50% reduction in TMDb API calls per navigation

**Action 4: Delete dead code**
- Remove files: `session_auth_enhanced.py`, `backup_manager.py`, `setup_production_env.py`
- Remove `profiling/` directory
- Remove `OptimizedMovieQueue` class from `scripts/movie_queue.py` (or entire file if nothing else is used)
- Move root-level test files into `tests/`

---

## Sprint 2 — Performance & Stability (Do Next)

| # | Action | Impact | Effort | Files Affected |
|---|--------|--------|--------|----------------|
| 5 | **Replace ORDER BY RAND() with efficient randomization** | 5 | 3 | `filter_backend.py`, possibly new migration |
| 6 | **Slim down session payloads** | 4 | 3 | `movie_navigator.py`, `movie_renderer.py` |
| 7 | **Share single TMDbHelper instance** | 3 | 1 | `scripts/movie.py`, `movie_service.py` |
| 8 | **Enforce request timeouts** | 4 | 1 | `routes.py` |
| 9 | **Schedule cache table refresh** | 3 | 1 | `app.py` (APScheduler job) |

### Details

**Action 5: Replace ORDER BY RAND()**
- Option A: Add a `rand_order` column to filtered query results (like cache tables already have)
- Option B: Use `WHERE id >= FLOOR(RAND() * (SELECT MAX(id) FROM table))` for single random picks
- Option C: Maintain a pre-shuffled ID table per common filter combination
- Benchmark each option against the 13-second baseline

**Action 6: Slim down session payloads**
- Change `MovieNavigator` to store only `{tconst, title, slug}` in `prev_stack` and `future_stack`
- Fetch full movie data from Redis cache or TMDb API on render
- Target: reduce session size from ~500 KB to ~5 KB per user

**Action 7: Share TMDbHelper**
- Pass `MovieManager.tmdb_helper` into `Movie` objects instead of letting each create its own
- This eliminates N httpx.AsyncClient pools per queue load

**Action 8: Enforce request timeouts**
- Wrap route handlers with `asyncio.wait_for(coro, timeout=_REQUEST_TIMEOUT)`
- Return 504 Gateway Timeout on expiration
- Log timeout events with correlation ID

**Action 9: Schedule cache refresh**
- Add APScheduler job in `app.py` lifespan to call `refresh_movie_caches()` stored procedure
- Run daily at off-peak hours
- Log refresh duration and row counts

---

## Sprint 3 — Security Right-Sizing & Cleanup

| # | Action | Impact | Effort | Files Affected |
|---|--------|--------|--------|----------------|
| 10 | **Remove cache encryption for public data** | 2 | 2 | `secure_cache.py`, `app.py` |
| 11 | **Fix X-Forwarded-For trusted proxy issue** | 3 | 1 | `session_security_enhanced.py`, `app.py` |
| 12 | **Standardize logging to get_logger()** | 2 | 1 | ~10 files |
| 13 | **Move rate limiting to Redis** | 3 | 2 | `routes.py`, `secure_pool.py` |
| 14 | **Remove stale config references** | 1 | 1 | `config/database.py`, `local_env_setup.py` |

### Details

**Action 10: Remove cache encryption**
- Replace `SecureCacheManager` (HMAC + Fernet) with a simple Redis cache wrapper
- Keep TTL-based expiration and key namespacing
- Remove ~600 lines of encryption/signing code
- If sensitive data is ever added later, re-evaluate

**Action 11: Fix trusted proxy**
- Add `TRUSTED_PROXIES` env var (comma-separated IP list)
- Only accept `X-Forwarded-For` from requests originating from trusted IPs
- Fall back to `request.remote_addr` for untrusted sources

**Action 12: Standardize logging**
- Replace all `logging.getLogger(__name__)` with `get_logger(__name__)`
- Replace f-string log formatting with `%s`-style lazy formatting
- Fix middleware.py root logger usage

**Action 13: Move rate limiting to Redis**
- Replace in-memory rate limit dicts with Redis INCR + EXPIRE
- This ensures rate limits work across multiple app instances
- Consider using `slowapi` or similar library instead of custom implementation

---

## Sprint 4 — Infrastructure Modernization

| # | Action | Impact | Effort | Files Affected |
|---|--------|--------|--------|----------------|
| 15 | **Upgrade Python to 3.11+** | 4 | 3 | `runtime.txt`, `requirements.txt`, CI config |
| 16 | **Fix CI/CD to test correct Python version** | 3 | 1 | `.github/workflows/` |
| 17 | **Add pyproject.toml test configuration** | 2 | 1 | New `pyproject.toml` or update existing |
| 18 | **Fix CI coverage target** | 2 | 1 | CI config |
| 19 | **Add FK constraints or import validation** | 2 | 2 | Migration scripts |

---

## Summary

| Sprint | Items | Estimated Effort | Cumulative Impact |
|--------|-------|-----------------|-------------------|
| Sprint 1 | 4 actions | 3-4 days | Eliminates silent failures, halves API calls, removes dead code |
| Sprint 2 | 5 actions | 5-7 days | Fixes 13s query, shrinks sessions 100x, adds timeouts |
| Sprint 3 | 5 actions | 4-5 days | Right-sizes security, fixes proxy trust, consistent logging |
| Sprint 4 | 5 actions | 4-5 days | Modern Python, reliable CI, data integrity |

**Total estimated effort:** 16-21 engineering days

**Expected outcome after all sprints:**
- Overall score improvement from 6.5/10 → 8.5/10
- ~50% reduction in TMDb API calls
- ~100x reduction in session storage
- ~2,500 lines of dead/over-engineered code removed
- Silent failures eliminated
- CI actually validates what runs in production
