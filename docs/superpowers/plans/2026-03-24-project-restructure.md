# Project Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize nextreel-lite from a flat 38-file root into a clean package structure with `movies/`, `infra/`, `session/`, `docs/`, and `ops/` directories.

**Architecture:** Move files into semantic packages, update all imports (including lazy imports and mock.patch targets), delete dead code, and verify with the full test suite after each phase.

**Tech Stack:** Python 3.11, Quart, pytest, git

---

## Phase 1: Delete Dead Weight

### Task 1: Delete deprecated root-level test stubs

**Files:**
- Delete: `test_ssl.py`, `test_security_enhanced.py`, `test_security_integration.py`, `test_query_performance.py`, `test_loki_logs.py`, `test_language_filter.py`, `test_enhanced_movie_data.py`, `test_2024_query.py`, `pool_security_test.py`, `cache_security_test.py`

- [ ] **Step 1: Delete all 10 deprecated test stubs**

```bash
rm test_ssl.py test_security_enhanced.py test_security_integration.py \
   test_query_performance.py test_loki_logs.py test_language_filter.py \
   test_enhanced_movie_data.py test_2024_query.py \
   pool_security_test.py cache_security_test.py
```

- [ ] **Step 2: Run tests to confirm nothing broke**

Run: `python3 -m pytest tests/ -x -q`
Expected: All tests pass (these files were stubs with `raise ImportError`)

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: delete 10 deprecated root-level test stubs"
```

### Task 2: Delete dead files and directories

**Files:**
- Delete: `src/` (empty abandoned directory)
- Delete: `profiling/` (entirely commented-out code)
- Delete: `next.config.js` (no Next.js in project)
- Delete: `firebase-debug.log` (no Firebase)
- Delete: `.production_secrets.json` (empty placeholder)
- Delete: `__init__.py` (root — unintentional package root)
- Delete: `backup_manager.py` (7-line stub)
- Delete: `performance_comparison.py` (7-line stub)
- Delete: `setup_production_env.py` (8-line stub)
- Delete: `secure_cache.py` (dead compat wrapper — `SecureCacheManager` and `cache_response` are never imported or used)
- Delete: `db_utils.py` (24-line legacy wrapper; only imported by `scripts/validate_referential_integrity.py` which we'll update)

- [ ] **Step 1: Delete empty/stub/dead files**

```bash
rm -rf src/ profiling/
rm next.config.js firebase-debug.log .production_secrets.json __init__.py
rm backup_manager.py performance_comparison.py setup_production_env.py
rm secure_cache.py
```

- [ ] **Step 2: Update the one file that imported `db_utils`**

File: `scripts/validate_referential_integrity.py:81` — change lazy import from `db_utils.init_pool` to `database.pool.init_pool`:

```python
# OLD:
from db_utils import init_pool
# NEW:
from database.pool import init_pool
```

- [ ] **Step 3: Delete `db_utils.py`**

```bash
rm db_utils.py
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: delete dead files, stubs, and unused modules"
```

### Task 3: Move HTML mockups to docs/mockups/

**Files:**
- Move: `mockup_movie_page.html`, `mockup_redesign_A.html`, `mockup_redesign_B.html`, `mockup_redesign_C.html`, `preview_movie_card.html`

- [ ] **Step 1: Move mockups**

```bash
mkdir -p docs/mockups
mv mockup_movie_page.html mockup_redesign_A.html mockup_redesign_B.html \
   mockup_redesign_C.html preview_movie_card.html docs/mockups/
```

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "chore: move HTML mockups to docs/mockups/"
```

---

## Phase 2: Create `movies/` Package (rename `scripts/`)

### Task 4: Rename `scripts/` to `movies/` and move actual scripts out

The `scripts/` directory contains the domain layer (Movie, TMDbHelper, filter_backend). Rename it to `movies/`. The actual runnable scripts (`update_ratings.py`, `validate_referential_integrity.py`) stay in a new `scripts/` directory.

**Files:**
- Rename: `scripts/` → `movies/`
- Move out: `movies/update_ratings.py` → `scripts/update_ratings.py`
- Move out: `movies/validate_referential_integrity.py` → `scripts/validate_referential_integrity.py`
- Create: `scripts/__init__.py` (empty)
- Rename: `movies/filter_backend.py` → `movies/query_builder.py`
- Update imports in 16+ files

- [ ] **Step 1: Rename the package**

```bash
git mv scripts movies
```

- [ ] **Step 2: Move actual scripts back to a new scripts/ directory**

```bash
mkdir -p scripts
git mv movies/update_ratings.py scripts/update_ratings.py
git mv movies/validate_referential_integrity.py scripts/validate_referential_integrity.py
touch scripts/__init__.py
```

- [ ] **Step 3: Rename filter_backend.py to query_builder.py**

```bash
git mv movies/filter_backend.py movies/query_builder.py
```

- [ ] **Step 4: Update the relative import inside movies/query_builder.py**

The `.interfaces` relative import still works. No change needed.

- [ ] **Step 5: Update internal import in movies/movie.py**

```python
# OLD:
from scripts.tmdb_client import TMDbHelper
# NEW:
from movies.tmdb_client import TMDbHelper
```

- [ ] **Step 6: Update all `from scripts.` imports across the codebase**

Files to update (replace `scripts.` with `movies.` and `filter_backend` with `query_builder` where applicable):

| File | Old Import | New Import |
|------|-----------|------------|
| `movie_navigator.py:92` | `from scripts.movie import Movie` | `from movies.movie import Movie` |
| `movie_navigator.py:129` | `from scripts.movie import Movie` | `from movies.movie import Movie` |
| `movie_service.py:7` | `from scripts.filter_backend import ImdbRandomMovieFetcher, MovieQueryBuilder` | `from movies.query_builder import ImdbRandomMovieFetcher, MovieQueryBuilder` |
| `movie_service.py:11` | `from scripts.tmdb_client import TMDbHelper` | `from movies.tmdb_client import TMDbHelper` |
| `movie_renderer.py:6` | `from scripts.movie import Movie` | `from movies.movie import Movie` |
| `update_languages_from_tmdb.py:13` | `from scripts.tmdb_client import TMDbHelper` | `from movies.tmdb_client import TMDbHelper` |
| `scripts/validate_referential_integrity.py` | Any `scripts.` refs become `movies.` | Update as needed |

- [ ] **Step 7: Update all test file imports**

| Test File | Old Import | New Import |
|-----------|-----------|------------|
| `tests/test_data_access.py:7` | `from scripts.filter_backend import ImdbRandomMovieFetcher` | `from movies.query_builder import ImdbRandomMovieFetcher` |
| `tests/test_data_access.py:8` | `from scripts.movie import Movie` | `from movies.movie import Movie` |
| `tests/test_tmdb_client.py:4` | `from scripts.tmdb_client import TMDbHelper` | `from movies.tmdb_client import TMDbHelper` |
| `tests/test_movie_data.py:8` | `from scripts.movie import Movie` | `from movies.movie import Movie` |
| `tests/test_movie_data.py:21` | `from scripts.tmdb_client import TMDbHelper` | `from movies.tmdb_client import TMDbHelper` |
| `tests/test_query_builder.py:8` | `from scripts.filter_backend import MovieQueryBuilder` | `from movies.query_builder import MovieQueryBuilder` |
| `tests/test_tmdb_parsers.py:8` | `from scripts.tmdb_client import TMDbHelper` | `from movies.tmdb_client import TMDbHelper` |
| `tests/test_filter_backend_extract.py:2` | `from scripts.filter_backend import extract_movie_filter_criteria` | `from movies.query_builder import extract_movie_filter_criteria` |
| `tests/test_filter_backend.py:4` | `from scripts.filter_backend import ImdbRandomMovieFetcher` | `from movies.query_builder import ImdbRandomMovieFetcher` |

- [ ] **Step 8: Update mock.patch targets in tests**

Search all test files for `patch("scripts.` and update to `patch("movies.` (and `filter_backend` to `query_builder`).

- [ ] **Step 9: Update the lazy import in scripts/tmdb_client.py (now movies/tmdb_client.py)**

```python
# movies/tmdb_client.py:17 (lazy import inside a method)
# OLD:
from secrets_manager import secrets_manager
# This will be updated in Phase 3 — leave as-is for now
```

- [ ] **Step 10: Run tests**

Run: `python3 -m pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor: rename scripts/ to movies/, rename filter_backend to query_builder"
```

---

## Phase 3: Create `infra/` Package

### Task 5: Create infra/ and move infrastructure files

**Files:**
- Create: `infra/__init__.py`
- Move: `secure_pool.py` → `infra/pool.py`
- Move: `simple_cache.py` → `infra/cache.py`
- Move: `ssl_validator.py` → `infra/ssl.py`
- Move: `secrets_manager.py` → `infra/secrets.py`
- Move: `metrics_collector.py` → `infra/metrics.py`
- Move: `database/errors.py` → `infra/errors.py`
- Merge: `database/pool.py` content into `infra/pool.py` (the wrapper class `DatabaseConnectionPool` and the global helpers `init_pool`, `get_pool`, `close_pool` get appended to the file containing `SecureConnectionPool`)
- Delete: `database/` directory (absorbed into infra/)

- [ ] **Step 1: Create infra/ package**

```bash
mkdir -p infra
```

- [ ] **Step 2: Move files with git mv**

```bash
git mv secure_pool.py infra/pool.py
git mv simple_cache.py infra/cache.py
git mv ssl_validator.py infra/ssl.py
git mv secrets_manager.py infra/secrets.py
git mv metrics_collector.py infra/metrics.py
git mv database/errors.py infra/errors.py
```

- [ ] **Step 3: Merge database/pool.py (DatabaseConnectionPool wrapper) into infra/pool.py**

Append the `DatabaseConnectionPool` class and its global helpers (`init_pool`, `get_pool`, `close_pool`) from `database/pool.py` to the end of `infra/pool.py`. Update the internal import from `from secure_pool import SecureConnectionPool` to a local reference (it's now in the same file). Update `from database.errors import DatabaseError` to `from infra.errors import DatabaseError`.

- [ ] **Step 4: Create infra/__init__.py with re-exports**

```python
"""Infrastructure layer — database pools, caching, SSL, secrets, metrics."""

from infra.pool import DatabaseConnectionPool, init_pool, get_pool, close_pool
from infra.errors import DatabaseError

__all__ = [
    "DatabaseConnectionPool",
    "init_pool",
    "get_pool",
    "close_pool",
    "DatabaseError",
]
```

- [ ] **Step 5: Delete the old database/ package**

```bash
rm database/pool.py database/__init__.py
rmdir database
```

- [ ] **Step 6: Update all imports of `from secure_pool`**

Only one file: `database/pool.py` — already merged into `infra/pool.py`, so this is handled.

- [ ] **Step 7: Update all imports of `from simple_cache`**

| File | Old Import | New Import |
|------|-----------|------------|
| `app.py:27` | `from simple_cache import SimpleCacheManager` | `from infra.cache import SimpleCacheManager` |
| `movie_renderer.py:47` | `from simple_cache import CacheNamespace` (lazy) | `from infra.cache import CacheNamespace` |
| `movie_navigator.py:52` | `from simple_cache import CacheNamespace` (lazy) | `from infra.cache import CacheNamespace` |
| `movie_navigator.py:81` | `from simple_cache import CacheNamespace` (lazy) | `from infra.cache import CacheNamespace` |
| `tests/test_simple_cache.py:8` | `from simple_cache import CacheNamespace, SimpleCacheManager` | `from infra.cache import CacheNamespace, SimpleCacheManager` |

- [ ] **Step 8: Update mock.patch targets for simple_cache**

| File | Old Patch | New Patch |
|------|----------|-----------|
| `tests/test_simple_cache.py:29` | `patch("simple_cache.aioredis.Redis")` | `patch("infra.cache.aioredis.Redis")` |
| `tests/test_simple_cache.py:41` | `patch("simple_cache.aioredis.from_url")` | `patch("infra.cache.aioredis.from_url")` |
| `tests/test_simple_cache.py:68` | `patch("simple_cache.aioredis.Redis")` | `patch("infra.cache.aioredis.Redis")` |
| `tests/test_simple_cache.py:79` | `patch("simple_cache.aioredis.from_url")` | `patch("infra.cache.aioredis.from_url")` |
| `tests/test_simple_cache.py:99` | `patch("simple_cache.aioredis.from_url")` | `patch("infra.cache.aioredis.from_url")` |

- [ ] **Step 9: Update all imports of `from secrets_manager`**

| File | Old Import | New Import |
|------|-----------|------------|
| `config/api.py:3` | `from secrets_manager import secrets_manager` | `from infra.secrets import secrets_manager` |
| `app.py:21` | `from secrets_manager import secrets_manager` | `from infra.secrets import secrets_manager` |
| `movies/tmdb_client.py:17` | `from secrets_manager import secrets_manager` (lazy) | `from infra.secrets import secrets_manager` |
| `tests/test_secrets_manager.py:8` | `from secrets_manager import SecretsManager` | `from infra.secrets import SecretsManager` |

- [ ] **Step 10: Update all imports of `from metrics_collector`**

| File | Old Import | New Import |
|------|-----------|------------|
| `app.py:17` | `from metrics_collector import MetricsCollector, setup_metrics_middleware` | `from infra.metrics import MetricsCollector, setup_metrics_middleware` |
| `routes.py:30` | `from metrics_collector import user_actions_total` | `from infra.metrics import user_actions_total` |
| `routes.py:189` | `from metrics_collector import metrics_endpoint` (lazy) | `from infra.metrics import metrics_endpoint` |
| `session_auth.py:45` | `from metrics_collector import user_sessions_total` (lazy) | `from infra.metrics import user_sessions_total` |
| `tests/test_metrics_collector.py:9` | `from metrics_collector import MetricsCollector` | `from infra.metrics import MetricsCollector` |

- [ ] **Step 11: Update all imports of `from database.`**

| File | Old Import | New Import |
|------|-----------|------------|
| `settings.py:44` | `from database.pool import DatabaseConnectionPool, init_pool, get_pool, close_pool` | `from infra.pool import DatabaseConnectionPool, init_pool, get_pool, close_pool` |
| `movies/movie.py:5` | `from database.errors import DatabaseError` | `from infra.errors import DatabaseError` |
| `movies/query_builder.py:7` | `from database.errors import DatabaseError` | `from infra.errors import DatabaseError` |
| `scripts/validate_referential_integrity.py` | `from database.pool import init_pool` (updated in Task 2) | `from infra.pool import init_pool` |
| `tests/test_data_access.py:5` | `from database.errors import DatabaseError` | `from infra.errors import DatabaseError` |
| `tests/test_data_access.py:6` | `from database.pool import DatabaseConnectionPool` | `from infra.pool import DatabaseConnectionPool` |
| `tests/test_database_pool.py:8` | `from database.errors import DatabaseError` | `from infra.errors import DatabaseError` |
| `tests/test_database_pool.py:37` | `from database.pool import DatabaseConnectionPool` (lazy) | `from infra.pool import DatabaseConnectionPool` |

- [ ] **Step 12: Update mock.patch targets for database**

| File | Old Patch | New Patch |
|------|----------|-----------|
| `tests/test_database_pool.py:24` | `patch("database.pool.SecureConnectionPool")` | `patch("infra.pool.SecureConnectionPool")` |

- [ ] **Step 13: Update internal imports within infra/pool.py**

Inside the merged `infra/pool.py`:
- Remove `from secure_pool import SecureConnectionPool, SecurePoolConfig` (now same file)
- Change `from database.errors import DatabaseError` to `from infra.errors import DatabaseError`
- Change `from config.database import DatabaseConfig` — no change needed (config stays)

- [ ] **Step 14: Run tests**

Run: `python3 -m pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 15: Commit**

```bash
git add -A
git commit -m "refactor: create infra/ package, merge database/ into it"
```

---

## Phase 4: Create `session/` Package

### Task 6: Create session/ and move session files

**Files:**
- Create: `session/__init__.py`
- Move: `session_keys.py` → `session/keys.py`
- Move: `session_auth.py` → `session/auth.py`
- Move: `session_security_enhanced.py` → `session/security.py`

- [ ] **Step 1: Create session/ package and move files**

```bash
mkdir -p session
git mv session_keys.py session/keys.py
git mv session_auth.py session/auth.py
git mv session_security_enhanced.py session/security.py
```

- [ ] **Step 2: Create session/__init__.py**

```python
"""Session management — keys, auth, and security."""
```

- [ ] **Step 3: Update all `from session_keys` imports (11 files)**

| File | Old Import | New Import |
|------|-----------|------------|
| `routes.py:31` | `from session_keys import USER_ID_KEY, CURRENT_FILTERS_KEY` | `from session.keys import USER_ID_KEY, CURRENT_FILTERS_KEY` |
| `movie_service.py:14` | `from session_keys import (CURRENT_MOVIE_KEY, ...)` | `from session.keys import (CURRENT_MOVIE_KEY, ...)` |
| `movie_navigator.py:8` | `from session_keys import (WATCH_QUEUE_KEY, ...)` | `from session.keys import (WATCH_QUEUE_KEY, ...)` |
| `session/auth.py:15` | `from session_keys import (CREATED_AT_KEY, ...)` | `from session.keys import (CREATED_AT_KEY, ...)` |
| `movie_renderer.py:7` | `from session_keys import CURRENT_MOVIE_KEY` | `from session.keys import CURRENT_MOVIE_KEY` |
| `session/security.py:24` | `from session_keys import (...)` | `from session.keys import (...)` |
| `tests/test_session_auth.py:8` | `from session_keys import ...` | `from session.keys import ...` |
| `tests/test_movie_navigator_extended.py:12` | `from session_keys import (...)` | `from session.keys import (...)` |
| `tests/test_session_security.py:4` | `from session_keys import (...)` | `from session.keys import (...)` |
| `tests/test_movie_service.py:8` | `from session_keys import USER_ID_KEY` | `from session.keys import USER_ID_KEY` |
| `tests/test_movie_navigator.py:5` | `from session_keys import WATCH_QUEUE_KEY` | `from session.keys import WATCH_QUEUE_KEY` |

- [ ] **Step 4: Update all `from session_auth` imports (2 files)**

| File | Old Import | New Import |
|------|-----------|------------|
| `app.py:22` | `from session_auth import init_session` | `from session.auth import init_session` |
| `tests/test_session_auth.py:7` | `from session_auth import _default_criteria, init_session` | `from session.auth import _default_criteria, init_session` |

- [ ] **Step 5: Update all `from session_security_enhanced` imports (2 files)**

| File | Old Import | New Import |
|------|-----------|------------|
| `app.py:24` | `from session_security_enhanced import EnhancedSessionSecurity, add_security_headers` | `from session.security import EnhancedSessionSecurity, add_security_headers` |
| `tests/test_session_security.py:11` | `from session_security_enhanced import EnhancedSessionSecurity` | `from session.security import EnhancedSessionSecurity` |

- [ ] **Step 6: Update internal imports within session/ files**

In `session/auth.py`:
- `from session_keys import ...` → `from session.keys import ...` (already done in step 3)
- `from metrics_collector import ...` → `from infra.metrics import ...` (already done in Phase 3)

In `session/security.py`:
- `from session_keys import ...` → `from session.keys import ...` (already done in step 3)

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: create session/ package from session_*.py files"
```

---

## Phase 5: Organize Docs and Ops

### Task 7: Move documentation and ops files

**Files:**
- Move to `docs/`: `ARCHITECTURE_AUDIT.md`, `ADR-001-ARCHITECTURE-AUDIT.md`, `SYSTEM_DESIGN_REVIEW.md`, `TECH_DEBT_REPORT.md`, `THREAT_MODEL.md`, `PERFORMANCE_IMPROVEMENTS.md`, `ACTION_PLAN.md`, `GRAFANA_SETUP.md`
- Move to `ops/`: `grafana-dashboard.json`, `nextreel_logs_dashboard.json`, `prometheus.yml.example`, `production_db_optimization.sql`, `project_optimized_indexes.sql`, `index_maintenance.sh`
- Move: `add_movie_slugs.py`, `check_2024_movies.py`, `update_languages_from_tmdb.py`, `local_env_setup.py` → `scripts/`

- [ ] **Step 1: Move docs**

```bash
git mv ARCHITECTURE_AUDIT.md ADR-001-ARCHITECTURE-AUDIT.md \
       SYSTEM_DESIGN_REVIEW.md TECH_DEBT_REPORT.md THREAT_MODEL.md \
       PERFORMANCE_IMPROVEMENTS.md ACTION_PLAN.md GRAFANA_SETUP.md docs/
```

- [ ] **Step 2: Move ops files**

```bash
mkdir -p ops
git mv grafana-dashboard.json nextreel_logs_dashboard.json \
       prometheus.yml.example production_db_optimization.sql \
       project_optimized_indexes.sql index_maintenance.sh ops/
```

- [ ] **Step 3: Move standalone scripts**

```bash
git mv add_movie_slugs.py scripts/
git mv check_2024_movies.py scripts/
git mv update_languages_from_tmdb.py scripts/
git mv local_env_setup.py scripts/
```

- [ ] **Step 4: Update import in scripts/update_languages_from_tmdb.py**

```python
# OLD:
from scripts.tmdb_client import TMDbHelper
# NEW:
from movies.tmdb_client import TMDbHelper
```

(This may have already been handled in Phase 2 — verify.)

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: organize docs/, ops/, and scripts/ directories"
```

---

## Phase 6: Update CLAUDE.md

### Task 8: Update CLAUDE.md to reflect new structure

- [ ] **Step 1: Update the Architecture section**

Replace the old file tree in CLAUDE.md with the new structure:

```
app.py                  # Entry point — creates Quart app, wires dependencies
routes.py               # All HTTP endpoints (Blueprint "main")
movie_service.py        # MovieManager facade — coordinates navigation + rendering
movie_navigator.py      # Session-based prev/next stacks, queue management
movie_renderer.py       # Template rendering for movie detail pages
movies/
  movie.py              # Movie class — fetches and assembles movie data from TMDb + DB
  tmdb_client.py        # TMDbHelper — async HTTP client with circuit breaker
  query_builder.py      # SQL query builder for random movie fetching
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

- [ ] **Step 2: Update import path references**

Update any `from scripts.` → `from movies.`, `from database.` → `from infra.`, etc. in the CLAUDE.md examples.

- [ ] **Step 3: Remove references to deleted files**

Remove mentions of `db_utils.py`, `secure_cache.py`, `src/nextreel/`, etc.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect new project structure"
```

---

## Final Verification

### Task 9: Full test suite and import validation

- [ ] **Step 1: Run full test suite with verbose output**

Run: `python3 -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Check for any remaining references to old paths**

```bash
grep -rn "from scripts\." --include="*.py" .
grep -rn "from database\." --include="*.py" .
grep -rn "from secure_pool" --include="*.py" .
grep -rn "from simple_cache" --include="*.py" .
grep -rn "from secure_cache" --include="*.py" .
grep -rn "from session_keys" --include="*.py" .
grep -rn "from session_auth" --include="*.py" .
grep -rn "from session_security" --include="*.py" .
grep -rn "from secrets_manager" --include="*.py" .
grep -rn "from metrics_collector" --include="*.py" .
grep -rn "from db_utils" --include="*.py" .
```

Expected: No matches (all old import paths eliminated)

- [ ] **Step 3: Verify the app starts**

Run: `python3 -c "from app import create_app; print('OK')"`
Expected: Prints "OK" without import errors

- [ ] **Step 4: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix: resolve any remaining import path issues"
```
