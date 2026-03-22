# Architecture Audit — NextReel-Lite

**Date:** 2026-03-20
**Scope:** Full codebase review across structural, data, auth, and assumption dimensions

---

## Prioritized Findings Table

| # | Severity | Category | Finding | File:Line | Fix Recommendation |
|---|----------|----------|---------|-----------|-------------------|
| 1 | **Critical** | Structural | **3 competing session auth implementations** — `session_auth.py`, `session_auth_enhanced.py`, and `session_security_enhanced.py` all define independent session creation/validation with conflicting logic. Both `session_auth.init_session()` AND `EnhancedSessionSecurity._before_request_handler()` run on every request, racing to create sessions. | `app.py:117,136` / `session_security_enhanced.py:183` / `session_auth.py:58` | Delete `session_auth_enhanced.py` (only used in one test). Merge `session_auth.py` into `session_security_enhanced.py`. Single session lifecycle owner. |
| 2 | **Critical** | Structural | **Dual Redis initialization** — `setup_redis()` and `startup()` are both registered as `@app.before_serving` handlers. Both create Redis connections. `startup()` also calls `movie_manager.start()` which was already called in the `lifespan()` context manager, creating a double-init race. | `app.py:76-110` / `app.py:207-227` / `app.py:164-178` | Consolidate all startup into `lifespan()`. Remove duplicate `@before_serving startup()`. Remove `setup_redis()` and fold Redis init into lifespan. |
| 3 | **Critical** | Structural | **Conflicting shutdown paths** — `lifespan()` yield cleanup AND `@after_serving cleanup()` both call `_shutdown_resources()`. Depending on Quart's execution order, resources may be double-closed. | `app.py:180-182` / `app.py:269-273` | Pick one shutdown mechanism. Since `lifespan` is the modern Quart pattern, remove the `@after_serving cleanup()` handler. |
| 4 | **High** | Auth | **`/metrics` and `/ready` expose internal state without authentication** — Pool sizes, connection counts, query failure rates, circuit breaker state, and per-IP/per-user connection counts are publicly accessible. | `routes.py:72-106` | Add `require_secure_session` decorator or IP-allowlist to `/metrics` and `/ready`. These endpoints leak operational intelligence. |
| 5 | **High** | Structural | **Error handling swallows exceptions silently** — `DatabaseQueryExecutor.execute_async_query()` catches all exceptions and returns `None`. Callers (e.g., `filter_backend.py:199`, `movie.py:89`) don't distinguish "no results" from "query failed," leading to silent data loss. | `db_utils.py:78-84` | Raise exceptions from `execute_async_query()`. Let callers handle errors explicitly, or return a Result type that distinguishes empty vs. error. |
| 6 | **High** | Structural | **`OptimizedMovieQueue` is dead code** — Fully implemented class (500+ lines) with Redis cache integration, but never imported or used by the application. The actual queue logic lives inline in `MovieNavigator._load_movies_into_queue()`. | `scripts/movie_queue.py:50` | Either adopt `OptimizedMovieQueue` as the queue implementation or delete it. Current state is confusing dead weight. |
| 7 | **High** | Structural | **`session_auth_enhanced.py` is dead code** — `SessionSecurityManager` is never imported by the application. Only referenced in `tests/test_session_security.py`. | `session_auth_enhanced.py:31` | Delete the file. Its functionality is superseded by `session_security_enhanced.py`. |
| 8 | **High** | Data | **Cache tables have no automated refresh** — `popular_movies_cache` and `recent_movies_cache` are populated by a one-time SQL script. The `refresh_movie_caches()` stored procedure exists but is never called by the application. Cache staleness grows unbounded. | `production_db_optimization.sql:210-268` | Add a scheduled job (cron or DB event scheduler) to call `refresh_movie_caches()` daily. Or refresh from the application on startup. |
| 9 | **High** | Data | **No foreign keys enforced** — All table relationships (`title.basics` ↔ `title.ratings`, `title.principals` ↔ `name.basics`, etc.) are join-only with no FK constraints. Orphan records in `title.ratings` or `title.principals` will silently produce incorrect results. | `production_db_optimization.sql:19-23` | Add `FOREIGN KEY (tconst) REFERENCES title.basics(tconst)` constraints on `title.ratings`, `title.crew`, `title.principals`. Consider ON DELETE CASCADE. |
| 10 | **High** | Assumptions | **Hardcoded `max_year: 2023` in default criteria** — New users get criteria capped at 2023, meaning 2024+ movies are excluded by default. This was likely set at development time and never updated. | `session_auth.py:25` / `routes.py:239` | Change to dynamic year: `datetime.now().year`. Both `DEFAULT_CRITERIA` in `session_auth.py:25` and the inline criteria in `routes.py:239` need updating. |
| 11 | **High** | Auth | **`/handle_new_user` has no CSRF protection and uses GET** — Creates a new user identity and sets session state via GET request. Any page can trigger this via an `<img>` tag or redirect. | `routes.py:232-248` | Change to POST method. Add CSRF token validation. |
| 12 | **Medium** | Structural | **`local_env_setup.py` references `USER_DB_*` env vars** for a `UserAccounts` database that doesn't exist anywhere else in the codebase. Dead configuration for an abandoned feature. | `local_env_setup.py:24-27,33-36` | Remove `USER_DB_*` references. They reference infrastructure that was never built. |
| 13 | **Medium** | Structural | **`backup_manager.py`, `profiling/profiling.py`, `setup_production_env.py` are orphaned** — Never imported by any module in the application. | `backup_manager.py`, `profiling/profiling.py`, `setup_production_env.py` | Audit and remove if unused, or document their operational purpose and move to a `scripts/` or `ops/` directory. |
| 14 | **Medium** | Structural | **Competing logging patterns** — Some modules use `get_logger(__name__)` from `logging_config.py`, others use `logging.getLogger(__name__)` directly. `middleware.py:20` uses `logging.info()` (root logger) instead of a module logger. | `middleware.py:20` / `session_security_enhanced.py:44` / `secrets_manager.py:7` | Standardize all modules to use `get_logger(__name__)` from `logging_config.py`. |
| 15 | **Medium** | Structural | **Mixed f-string and %-style logging** — Security-sensitive logs in `session_security_enhanced.py` use f-strings (eager evaluation), while `routes.py` mixes both styles. F-string logging evaluates arguments even when the log level is disabled. | `session_security_enhanced.py:313,416,459` / `routes.py:139-141,157` | Standardize on `%`-style for all `logger.*()` calls per Python logging best practices. |
| 16 | **Medium** | Data | **`ORDER BY RAND()` on non-cache tables** — For queries that don't hit the cache tables, `ORDER BY RAND()` forces a full table scan and filesort on potentially millions of rows. | `scripts/filter_backend.py:234,237` | Use the pre-computed `rand_order` column approach (already done for `popular_movies_cache`) for all query paths. Or use a sampling strategy. |
| 17 | **Medium** | Data | **Cache table schema drift** — `popular_movies_cache` has columns `originalTitle`, `isAdult`, `endYear`, `runtimeMinutes` that `recent_movies_cache` lacks. Code using `SELECT *` from cache tables gets different column sets depending on which cache is hit. | `production_db_optimization.sql:75-97,132-149` | Align schemas of both cache tables, or stop using `SELECT *` and specify explicit column lists. |
| 18 | **Medium** | Auth | **Session cookie name conflict** — `SessionConfig` sets `SESSION_COOKIE_NAME = "session"`, but `EnhancedSessionSecurity._configure_secure_settings()` overrides it to `__Host-session` in production. The override happens at app init, but `routes.py:56` references `current_app.config["SESSION_COOKIE_NAME"]` for logout, which will get the overridden value. No bug today, but fragile coupling. | `config/session.py:14` / `session_security_enhanced.py:171` | Define cookie name in one place only. Remove the override in `_configure_secure_settings()` and set it in `SessionConfig` conditionally. |
| 19 | **Medium** | Auth | **Fingerprint uses `X-Forwarded-For` without trusted proxy validation** — An attacker can spoof `X-Forwarded-For` to bypass IP-based fingerprinting, potentially fixating another user's session fingerprint. | `session_security_enhanced.py:245-247` / `session_auth_enhanced.py:77` | Configure trusted proxy list. Only accept `X-Forwarded-For` from known proxy IPs. Use Quart's `ProxyFix` middleware. |
| 20 | **Medium** | Structural | **Circular import risk** — `session_keys.py` imports `from quart import session` at module level. `routes.py` imports from `session_keys`. `app.py` imports from `routes`. If session_keys is imported before the Quart app is created, this may fail in certain test configurations. | `session_keys.py:8` | Move `session` usage out of the module-level import. The key constants don't need the session import; only `reset_movie_stacks()` and `init_movie_stacks()` do — consider lazy import. |
| 21 | **Medium** | Data | **Migrations are not reversible** — `production_db_optimization.sql` uses `DROP TABLE IF EXISTS` and `TRUNCATE` with no rollback script. A failed partial execution leaves the database in an inconsistent state. | `production_db_optimization.sql:73,131` | Split into numbered migration files with both `up` and `down` scripts. Consider a migration tool (Alembic, Flyway, or manual versioned scripts). |
| 22 | **Medium** | Structural | **`movie_service.py` re-exposes all private methods** of `MovieNavigator` as public API for "backward compat." This defeats encapsulation and means any internal refactor to MovieNavigator is a breaking change. | `movie_service.py:107-124` | Remove delegating methods. Update callers to use `_navigator` directly or provide proper public API. |
| 23 | **Low** | Structural | **Test files at project root** — 8 test files (`test_*.py`) live at project root alongside production code instead of in `tests/`. | `test_query_performance.py`, `test_2024_query.py`, `test_language_filter.py`, etc. | Move all test files into `tests/` directory. |
| 24 | **Low** | Structural | **`db_utils.py` mixes SQL query definitions with executor class** — Query constants and the `DatabaseQueryExecutor` class share the same file, violating single responsibility. | `db_utils.py:1-57` (queries) / `db_utils.py:72-132` (executor) | Extract query constants to `queries.py`. Keep only `DatabaseQueryExecutor` in `db_utils.py`. |
| 25 | **Low** | Data | **`LIMIT 50000` on cache population is arbitrary** — No documentation on why 50k was chosen or what happens when the movie catalog exceeds this limit. Movies 50,001+ are silently excluded. | `production_db_optimization.sql:125` | Document the rationale. Consider removing the limit or making it configurable. Add monitoring for when the limit is hit. |
| 26 | **Low** | Assumptions | **`STACKHERO_DB_CONFIG = {}`** — Dead config in `config/database.py:18` referencing a hosting provider (StackHero) that's no longer used. | `config/database.py:18` | Remove the unused attribute. |
| 27 | **Low** | Assumptions | **Hardcoded TMDB movie ID 62 for default backdrop** — `MovieManager` uses TMDB ID 62 (2001: A Space Odyssey) as the default. If TMDB changes this ID or the movie is removed, the home page breaks. | `movie_service.py:38` | Make configurable via environment variable. Add fallback to a local static image. |
| 28 | **Low** | Structural | **`Movie` class creates a new `TMDbHelper` on every instantiation** — Each call to `Movie(tconst, db_pool)` (called per-movie in the queue) creates a fresh HTTP client. This wastes connection pool resources. | `scripts/movie.py:38` | Inject `TMDbHelper` as a constructor parameter. Share a single instance across all `Movie` objects. |

---

## Detailed Analysis by Dimension

### STRUCTURAL

**Competing Patterns:**
- **Session management** (Critical): Three independent implementations with overlapping responsibility. `session_auth.py` handles basic token+fingerprint, `session_auth_enhanced.py` adds rotation and timeouts, `session_security_enhanced.py` adds encryption, device fingerprinting, and Redis storage. All three are partially wired in, creating a Frankenstein auth layer.
- **Error handling**: Two competing strategies — `db_utils.py` swallows all exceptions and returns `None`, while `secure_pool.py` raises exceptions. Callers can't tell if `None` means "no data" or "database crashed."
- **Logging**: Three patterns in use (root logger, `logging.getLogger`, custom `get_logger`).

**Dead Code:**
- `session_auth_enhanced.py` — superseded, only test import
- `scripts/movie_queue.py` (`OptimizedMovieQueue`) — elaborate implementation never wired in
- `backup_manager.py`, `profiling/profiling.py`, `setup_production_env.py` — orphaned utility files
- `local_env_setup.py` `USER_DB_*` vars — references non-existent UserAccounts database
- `config/database.py` `STACKHERO_DB_CONFIG` — dead hosting reference
- `db_utils.py` `execute_async_query_legacy()` — legacy method, unclear if still called

**Circular Dependencies:** No hard circular imports detected, but `session_keys.py` importing `quart.session` at module level creates fragile coupling for test isolation.

**Architecture Match:** The codebase follows a rough service-layer pattern (Manager → Navigator/Renderer → Movie/Filter) but breaks it by having `MovieManager` re-expose all private internals of its delegates. There's no formal architectural boundary; domain logic, HTTP handling, and data access all import each other freely.

### DATA

**Normalization:** Appropriate for the use case. The IMDb dataset tables (`title.basics`, `title.ratings`, `title.crew`, `title.principals`, `name.basics`) follow IMDb's TSV schema. The cache tables (`popular_movies_cache`, `recent_movies_cache`) are intentional denormalization for performance.

**Orphan Records:** No FK constraints exist. JOINs between `title.basics` and `title.ratings` use INNER JOIN, so orphan ratings are silently excluded rather than causing errors. However, LEFT JOIN is used for the recent cache, which could surface orphan records with `COALESCE(rating, 0)`.

**Migrations:** Not reversible. No migration framework in use. Schema changes are one-shot SQL scripts that `DROP TABLE IF EXISTS` before recreating.

**Read/Write Separation:** The application is read-heavy with no user-generated writes to main tables. Write operations are limited to cache refresh (stored procedure, never called automatically) and one-time data maintenance scripts. No replica setup.

### AUTH & ACCESS CONTROL

**Auth Model:** Incrementally emerged. Three files represent three iterations of increasing sophistication, none fully replacing the prior version. The current runtime executes **both** `EnhancedSessionSecurity._before_request_handler()` and `session_auth.init_session()` on every request.

**Missing Authorization:**
- `/metrics` — exposes pool state, query stats, slow queries (unauthenticated)
- `/ready` — exposes database connection metrics (unauthenticated)
- `/handle_new_user` — GET endpoint that mutates session state (no CSRF)

**BOLA/IDOR:** Low risk because all data is public IMDb/TMDB movie information. No user-specific data (watchlists, ratings, reviews) is stored in the database. If user-specific features are added, the `/movie/<tconst>` endpoint has no ownership check and would be immediately vulnerable.

### ASSUMPTIONS

| Decision | Source | Conflict |
|----------|--------|----------|
| Default criteria `max_year: 2023` | Hardcoded at dev time | Excludes current-year movies for all new users |
| TMDB ID 62 for default backdrop | Hardcoded assumption | Breaks if TMDB changes/removes this entry |
| 50,000 movie cache limit | Assumed sufficient | No monitoring for when limit is exceeded |
| `ORDER BY RAND()` for non-cache queries | Quick implementation | 13+ second queries for 2024+ movies (documented in comments) |
| Three session auth files | Iterative AI-generated additions | Each iteration added a new file instead of replacing the prior one |
| `LIMIT {int(limit)}` via f-string in SQL | Assumed safe because `int()` cast | Should use parameterized query for consistency (even though `int()` cast prevents injection) |
| Redis always available | Assumed for session storage | Application crashes in dev if Redis is down, despite fallback code in some paths |

---

## Summary

The most impactful issues are **structural**: three competing session implementations and two competing startup/shutdown paths create unpredictable behavior in production. The data layer is well-optimized for reads but lacks operational automation (cache refresh, migration tooling). Auth is robust at the session level but missing at the route level for operational endpoints. The primary risk pattern is **accretion without removal** — new implementations are added alongside old ones rather than replacing them.
