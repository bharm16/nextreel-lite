# NextReel-Lite Tech Debt Report

**Date:** 2026-03-22
**Scoring:** Priority = (Impact + Risk) × (6 − Effort) — higher is worse

---

## Priority 1 — Critical (Fix This Sprint)

### 1. Three Competing Session Auth Implementations
**Type:** Architecture Debt | **Priority Score:** 40 | Impact: 5 | Risk: 5 | Effort: 2

You have three files all touching session management simultaneously:

- `session_auth.py` (106 lines) — basic token + fingerprint, used in `app.py` `before_request`
- `session_auth_enhanced.py` (248 lines) — `SessionSecurityManager` with rotation, **never imported by app code** (only tests)
- `session_security_enhanced.py` (711 lines) — `EnhancedSessionSecurity` with encryption, fingerprinting, HTTPS enforcement, **also initialized in `app.py`**

Both `session_auth.init_session()` and `EnhancedSessionSecurity`'s `before_request` handler run on every request. They each create tokens independently, meaning session state is being written twice with different logic. `session_auth_enhanced.py` is pure dead code — 248 lines that exist only because tests import it.

**Fix:** Pick one implementation. `session_security_enhanced.py` is the most complete. Delete `session_auth_enhanced.py` entirely, migrate `session_auth.py`'s `init_session` logic into the enhanced system, and update tests.

**Effort:** ~4 hours

---

### 2. Hardcoded `max_year: 2023` Excludes 3+ Years of Movies
**Type:** Code Debt | **Priority Score:** 40 | Impact: 5 | Risk: 5 | Effort: 2

Found in two places:
- `session_auth.py:23` — `DEFAULT_CRITERIA = {"max_year": 2023, ...}`
- `routes.py:346` — `handle_new_user()` endpoint hardcodes `"max_year": 2023`

Also in `filter_backend.py:29`, the cache optimization path checks `max_year <= 2023`.

Every new user since Jan 2024 has been silently excluded from seeing any movie released after 2023. This is a **product bug masquerading as tech debt**.

**Fix:** Replace with `datetime.now().year`. Remove the hardcoded cache check or make it dynamic.

**Effort:** ~30 minutes

---

### 3. CI/CD Pipeline Tests Against Python 3.6–3.8, App Runs 3.9
**Type:** Infrastructure Debt | **Priority Score:** 36 | Impact: 4 | Risk: 5 | Effort: 3

`.github/workflows/python-package.yml` tests against Python 3.6, 3.7, 3.8. The `runtime.txt` specifies `python-3.9.18`. The CI also uses `actions/checkout@v2` and `actions/setup-python@v2` (both deprecated since 2023), `codecov/codecov-action@v1` (deprecated), and the pytest coverage target is `--cov=tmdbsimple` — measuring coverage of a *third-party library*, not your own code.

Your CI is effectively testing nothing useful.

**Fix:** Update matrix to `[3.9, 3.10, 3.11]`, update all actions to v4, fix coverage target to `--cov=. --cov-report=xml`, install from `requirements.txt` instead of cherry-picking deps.

**Effort:** ~2 hours

---

### 4. `/metrics` and `/ready` Expose Internal State Without Auth
**Type:** Code Debt / Security | **Priority Score:** 36 | Impact: 4 | Risk: 5 | Effort: 3

`/ready` returns pool sizes, free connections, query counts, circuit breaker state, and average query times to anyone. `/metrics` exposes the full Prometheus scrape. The only protection is a basic in-memory rate limiter (30 req/min per IP), which is trivially bypassable.

**Fix:** Add a shared secret or IP whitelist. For Prometheus, use a bearer token or restrict to internal network.

**Effort:** ~2 hours

---

## Priority 2 — High (Fix Within 2 Sprints)

### 5. `ORDER BY RAND()` on Non-Cached Queries
**Type:** Code Debt / Performance | **Priority Score:** 30 | Impact: 5 | Risk: 4 | Effort: 3

`filter_backend.py:234,237` uses `ORDER BY RAND()` which causes a full table scan on the IMDb dataset. On a table with millions of rows, this is an O(n) sort per request.

**Fix:** Use a sampling strategy — either `WHERE id >= RAND() * MAX(id)` or maintain a pre-shuffled ID table. The codebase already has a cache table mechanism; extend it.

**Effort:** ~4 hours

---

### 6. ~1,900 Lines of Dead/Orphaned Code
**Type:** Code Debt | **Priority Score:** 28 | Impact: 4 | Risk: 3 | Effort: 3

Files with zero imports from application code:

| File | Lines | Status |
|------|-------|--------|
| `session_auth_enhanced.py` | 248 | Dead — only test imports |
| `backup_manager.py` | 775 | Orphaned — never imported |
| `setup_production_env.py` | 518 | Orphaned — never imported |
| `scripts/movie_queue.py` (`OptimizedMovieQueue`) | 508 | Dead — aliased as `MovieQueue` but never imported by app |
| Root-level test files (9 files) | ~350+ | Ad-hoc scripts, not in `tests/` |

This is ~1,900 lines of code that adds cognitive load, confuses grep results, and creates false confidence that functionality exists.

**Fix:** Delete `session_auth_enhanced.py` and `backup_manager.py`. Move or delete root-level test scripts. If `setup_production_env.py` is needed, document when/how it's used.

**Effort:** ~3 hours

---

### 7. `handle_new_user` Uses GET With No CSRF Protection
**Type:** Code Debt / Security | **Priority Score:** 28 | Impact: 4 | Risk: 4 | Effort: 2

`routes.py:340` — `@bp.route("/handle_new_user")` is a GET endpoint that creates a user, sets session state, and initializes movie queues. This is a state-mutating operation on a GET request with no CSRF token.

**Fix:** Change to POST, add CSRF validation via `_validate_csrf_from_form()`.

**Effort:** ~30 minutes

---

### 8. Dependencies Pinned to Dec 2023 / Jan 2024 Versions
**Type:** Dependency Debt | **Priority Score:** 27 | Impact: 3 | Risk: 5 | Effort: 2

All 35 dependencies in `requirements.txt` are pinned to versions from late 2023. Key concerns:
- `cryptography==41.0.7` — multiple CVEs issued since this version
- `jinja2==3.1.3` — security patches released since
- `werkzeug==3.0.1` — security patches released since
- Python 3.9 itself reaches EOL October 2025 (already past)

**Fix:** Run `pip-audit` against current pins. Update security-critical packages immediately. Plan Python 3.11+ migration.

**Effort:** ~4 hours for audit + update, ~2 days for Python version migration

---

### 9. Duplicate Shutdown Paths (Double Resource Cleanup)
**Type:** Architecture Debt | **Priority Score:** 24 | Impact: 3 | Risk: 4 | Effort: 3

`app.py` has both `lifespan()` and `@after_serving cleanup()` calling `_shutdown_resources()`. If Quart invokes both (which depends on server behavior), you get double-close on Redis pools, the DB pool, and the secure cache. This can cause `RuntimeError: connection pool is closed` on shutdown.

**Fix:** Remove the `@after_serving cleanup()` handler. Let `lifespan()` own the full lifecycle.

**Effort:** ~30 minutes (but test carefully)

---

## Priority 3 — Medium (Plan for Next Quarter)

### 10. Test Suite Is Fragmented and Incomplete
**Type:** Test Debt | **Priority Score:** 20 | Impact: 4 | Risk: 4 | Effort: 4

- 734 lines of structured tests in `tests/` (8 files)
- ~350+ lines of ad-hoc test scripts scattered at the project root (9 files)
- No `pytest.ini` or `pyproject.toml` for test configuration
- No integration tests for database connectivity
- CI coverage target measures the wrong package

**Fix:** Consolidate all tests into `tests/`. Add `pyproject.toml` with pytest config. Write integration test fixtures. Target 70%+ coverage on your own code.

**Effort:** ~2-3 days

---

### 11. Mixed Logging Patterns
**Type:** Code Debt | **Priority Score:** 16 | Impact: 2 | Risk: 3 | Effort: 3

Three logging patterns coexist:
- `logging_config.get_logger(__name__)` — structured logger with correlation IDs (correct)
- `logging.getLogger(__name__)` — standard logger, no correlation (used in `session_auth.py`, `session_auth_enhanced.py`)
- f-string formatting in log calls — e.g., `logger.error(f"Failed to start: {e}")` bypasses lazy evaluation

**Fix:** Replace all `logging.getLogger` with `get_logger`. Replace f-string log formatting with `%s` style.

**Effort:** ~2 hours

---

### 12. `MovieManager` Facade Leaks Internal Abstractions
**Type:** Architecture Debt | **Priority Score:** 16 | Impact: 3 | Risk: 2 | Effort: 3

`movie_service.py` has a `MovieManager` class that delegates to `MovieNavigator` and `MovieRenderer`, but then re-exposes private methods like `_get_user_stacks()`, `_mark_movie_seen()`, `_load_movies_into_queue()` as public pass-throughs (lines 128-138). This defeats the purpose of the facade pattern.

**Fix:** Remove the pass-through private method accessors. If external code needs them, make them properly public on the delegates.

**Effort:** ~2 hours

---

### 13. Two Redundant Scheduling Libraries
**Type:** Dependency Debt | **Priority Score:** 12 | Impact: 2 | Risk: 2 | Effort: 2

Both `schedule==1.2.0` and `APScheduler==3.10.4` are in requirements. Pick one. `APScheduler` is more capable; if you're only using `schedule` for simple jobs, consolidate.

**Fix:** Audit usage, remove the unused one.

**Effort:** ~1 hour

---

### 14. No Foreign Key Constraints on Database Tables
**Type:** Architecture Debt | **Priority Score:** 12 | Impact: 3 | Risk: 3 | Effort: 4

Per the existing `ARCHITECTURE_AUDIT.md`, the IMDb data tables have no foreign key constraints, risking orphan records and data integrity issues.

**Fix:** Add FK constraints with `ON DELETE CASCADE` where appropriate. Test with a staging copy first.

**Effort:** ~1 day (needs careful migration planning)

---

## Summary

| Priority | Items | Est. Total Effort |
|----------|-------|-------------------|
| P1 — Critical | 4 items | ~1 day |
| P2 — High | 5 items | ~3-4 days |
| P3 — Medium | 5 items | ~5-6 days |

**Top 3 bang-for-buck (highest priority, lowest effort):**
1. Fix `max_year: 2023` — 30 min, immediately improves product for every new user
2. Change `handle_new_user` to POST — 30 min, closes a security hole
3. Remove `@after_serving cleanup()` — 30 min, prevents double-close crashes

**Biggest risk if ignored:**
- Outdated dependencies (`cryptography` CVEs, Python 3.9 EOL)
- Session auth confusion (three implementations = three attack surfaces)
