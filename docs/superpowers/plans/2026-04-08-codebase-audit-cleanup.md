# Codebase Audit Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address every finding from the 2026-04-08 whole-codebase audit (~190 items across 40 files) without modifying any user-observable workflow or public behavior.

**Architecture:** Changes are grouped into 8 sequential phases: (0) shared helpers that unblock later duplication fixes, (1) bug/correctness fixes, (2) verified dead code removal, (3) public-API promotion (eliminate leaky `_private` access without breaking tests), (4) reuse consolidation, (5) efficiency, (6) quality/style, (7) CLAUDE.md sync. Lower layers (infra) are touched before higher layers (movies → routes → app) so that if a phase is paused mid-way the codebase is still coherent.

**Tech Stack:** Python 3.11/3.12, Quart, aiomysql, redis-py, arq, pytest-asyncio.

**Guardrails (apply to every task):**
- **No behavior change.** If a refactor would alter response bodies, log format visible to users, schema, or public function signatures, keep both the old and new names as aliases.
- **No autocommit.** Stop at the diff. Commit messages in this plan are *suggested*; the user runs `git commit` themselves.
- **Test before and after.** `python3 -m pytest tests/ -v` must be green before starting each task and green after finishing it.
- **Keep tests untouched unless the task is explicitly about tests.** Tests encode current behavior — preserving them is how we prove "no user-visible change".
- **CLAUDE.md invariants hold:** `%s` lazy logging, parameterized SQL, `get_environment()` for env detection, `ssl.CERT_REQUIRED`, no `.env*` edits.

**Findings traceability:** Every task below has a `Findings:` line listing the items it closes. When a task is done, the listed findings are resolved.

---

## Phase 0 — Foundation helpers (unlocks deduplication in later phases)

### Task 0.1: Add `env_int` / `env_float` helpers to `infra/time_utils.py`

**Files:**
- Modify: `infra/time_utils.py` (add alongside existing `env_bool`)
- Test: `tests/test_time_utils.py` (extend existing if present, else create)

**Findings:** 15+ hand-rolled env parsing sites across `infra/pool.py`, `infra/legacy_migration.py`, `infra/navigation_state.py`, `infra/worker_metrics.py`, `config/database.py`, `config/session.py`, `worker.py`, `app.py`.

- [ ] **Step 1:** Read `infra/time_utils.py` to find the existing `env_bool` signature and module structure.
- [ ] **Step 2:** Write failing tests in `tests/test_time_utils.py` covering: unset → default, valid int/float, empty string → default, whitespace, non-numeric string → default (with a warning logged), negative numbers, zero.

```python
import os
from unittest.mock import patch
from infra.time_utils import env_int, env_float

def test_env_int_unset_returns_default():
    with patch.dict(os.environ, {}, clear=True):
        assert env_int("FOO_NOT_SET", 42) == 42

def test_env_int_valid_value():
    with patch.dict(os.environ, {"FOO": "7"}):
        assert env_int("FOO", 0) == 7

def test_env_int_empty_string_returns_default():
    with patch.dict(os.environ, {"FOO": ""}):
        assert env_int("FOO", 5) == 5

def test_env_int_invalid_returns_default(caplog):
    with patch.dict(os.environ, {"FOO": "notanumber"}):
        assert env_int("FOO", 9) == 9

def test_env_float_valid():
    with patch.dict(os.environ, {"FOO": "0.25"}):
        assert env_float("FOO", 1.0) == 0.25

def test_env_float_invalid_returns_default():
    with patch.dict(os.environ, {"FOO": "bad"}):
        assert env_float("FOO", 3.14) == 3.14
```

- [ ] **Step 3:** Run tests, confirm they fail: `python3 -m pytest tests/test_time_utils.py -v`
- [ ] **Step 4:** Implement both helpers next to `env_bool`:

```python
def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        logger.warning("Invalid int for %s=%r; using default %d", name, raw, default)
        return default

def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        logger.warning("Invalid float for %s=%r; using default %r", name, raw, default)
        return default
```

- [ ] **Step 5:** Run tests, confirm green.
- [ ] **Step 6:** Suggested commit: `chore(time_utils): add env_int/env_float helpers`

---

### Task 0.2: Add `safe_cache_get_or_set` helper to `infra/cache.py`

**Files:**
- Modify: `infra/cache.py`
- Test: `tests/test_cache.py`

**Findings:** Cache wrapper triplication in `movies/query_builder.py:277-297`, `movies/candidate_store.py:220-237`, `movies/watched_store.py:26-34,74-103`.

- [ ] **Step 1:** Write failing test: a function that calls `safe_cache_get_or_set(namespace, key, loader, ttl)` returns cached value on hit, calls loader on miss, writes to cache after, swallows and logs on cache exceptions (loader still called).

```python
@pytest.mark.asyncio
async def test_safe_cache_get_or_set_hit(monkeypatch):
    cache = SimpleCacheManager()
    await cache.set(CacheNamespace.TEMP, "k", {"v": 1}, ttl=60)
    loader = AsyncMock()
    result = await cache.safe_get_or_set(CacheNamespace.TEMP, "k", loader, ttl=60)
    assert result == {"v": 1}
    loader.assert_not_awaited()

@pytest.mark.asyncio
async def test_safe_cache_get_or_set_miss_calls_loader():
    cache = SimpleCacheManager()
    loader = AsyncMock(return_value={"v": 2})
    result = await cache.safe_get_or_set(CacheNamespace.TEMP, "k2", loader, ttl=60)
    assert result == {"v": 2}
    loader.assert_awaited_once()

@pytest.mark.asyncio
async def test_safe_cache_get_or_set_swallows_redis_errors():
    cache = SimpleCacheManager()
    cache.get = AsyncMock(side_effect=RuntimeError("redis down"))
    loader = AsyncMock(return_value={"v": 3})
    result = await cache.safe_get_or_set(CacheNamespace.TEMP, "k3", loader, ttl=60)
    assert result == {"v": 3}
```

- [ ] **Step 2:** Run: `pytest tests/test_cache.py -v -k safe_cache_get_or_set` — expect FAIL.
- [ ] **Step 3:** Implement on `SimpleCacheManager`:

```python
async def safe_get_or_set(self, namespace, key, loader, ttl):
    try:
        cached = await self.get(namespace, key)
        if cached is not None:
            return cached
    except Exception as exc:
        logger.warning("cache get failed for %s:%s: %s", namespace, key, exc)
    value = await loader()
    try:
        if value is not None:
            await self.set(namespace, key, value, ttl=ttl)
    except Exception as exc:
        logger.warning("cache set failed for %s:%s: %s", namespace, key, exc)
    return value
```

- [ ] **Step 4:** Run tests, confirm green.
- [ ] **Step 5:** Suggested commit: `feat(cache): add safe_get_or_set single-flight helper`

---

### Task 0.3: Add `safe_emit` helper to `infra/metrics_groups.py`

**Files:**
- Modify: `infra/metrics_groups.py`
- Test: `tests/test_metrics.py`

**Findings:** Five hand-rolled `try/except: pass` metric wrappers in `infra/metrics.py:347-349`, `infra/worker_metrics.py:138,147,157`, `movies/projection_enrichment.py:17-24`, `movies/tmdb_client.py:215-250`.

- [ ] **Step 1:** Write failing tests: `safe_emit(callable, *args, **kwargs)` returns result on success, returns None and logs on exception.
- [ ] **Step 2:** Run, verify fail.
- [ ] **Step 3:** Implement:

```python
def safe_emit(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.debug("metric emit failed: %s", exc)
        return None
```

- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Suggested commit: `feat(metrics): add safe_emit wrapper`

---

### Task 0.4: Extract `build_mysql_ssl_context()` stateless helper in `infra/ssl.py`

**Files:**
- Modify: `infra/ssl.py`
- Test: `tests/test_ssl.py`

**Findings:** `infra/pool.py:182-195` re-implements `infra/ssl.py:106-137` verbatim.

- [ ] **Step 1:** Write failing test: `build_mysql_ssl_context(cert_path=None)` returns an `ssl.SSLContext` with `CERT_REQUIRED`, `check_hostname=False`, `minimum_version=TLSv1_2`, and the documented cipher string.

```python
import ssl as ssl_lib
from infra.ssl import build_mysql_ssl_context

def test_build_mysql_ssl_context_defaults():
    ctx = build_mysql_ssl_context(None)
    assert ctx.verify_mode == ssl_lib.CERT_REQUIRED
    assert ctx.check_hostname is False
    assert ctx.minimum_version == ssl_lib.TLSVersion.TLSv1_2
```

- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Add a module-level `build_mysql_ssl_context` that contains exactly the body currently in `SSLCertificateValidator.create_ssl_context` (ignoring `verify_mode` param which was dead). Have `SSLCertificateValidator.create_ssl_context` call the helper internally so existing instance-based callers still work.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Suggested commit: `refactor(ssl): extract build_mysql_ssl_context stateless helper`

---

### Task 0.5: Add `LruExpiringMap` helper to `infra/cache.py`

**Files:**
- Modify: `infra/cache.py`
- Test: `tests/test_cache.py`

**Findings:** Near-identical OrderedDict LRU-with-TTL in `infra/rate_limit.py:100-131` and `infra/metrics.py:183-277`.

- [ ] **Step 1:** Write failing tests for: set/get, TTL expiry (monkeypatch time), LRU eviction when exceeding max_keys, stale eviction before LRU eviction.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement:

```python
from collections import OrderedDict
import time as _time

class LruExpiringMap:
    def __init__(self, max_keys: int, ttl_seconds: float):
        self._max_keys = max_keys
        self._ttl = ttl_seconds
        self._data: OrderedDict[str, tuple[float, object]] = OrderedDict()

    def set(self, key, value):
        now = _time.monotonic()
        self._evict(now)
        self._data[key] = (now + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max_keys:
            self._data.popitem(last=False)

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        expires_at, value = entry
        if _time.monotonic() >= expires_at:
            self._data.pop(key, None)
            return default
        self._data.move_to_end(key)
        return value

    def _evict(self, now: float):
        expired = [k for k, (exp, _) in self._data.items() if now >= exp]
        for k in expired:
            self._data.pop(k, None)

    def __len__(self): return len(self._data)
```

- [ ] **Step 4:** Tests green.
- [ ] **Step 5:** Suggested commit: `feat(cache): add LruExpiringMap helper`

---

## Phase 1 — Bug / correctness fixes

### Task 1.1: ~~Restore `g.start_time` assignment~~ **SKIPPED — false positive**

**Resolution:** The auditor's grep missed `infra/metrics.py:315`, which sets `g.start_time = time.time()` inside the `before_request` hook registered by `setup_metrics_middleware` (wired in `app.py:481`). The slow-log path at `app.py:432-443` reads a value that is populated on every request. A TDD reproduction attempt passed without any edit — the path is live.

**Residual risk (documented, not actioned):** The coupling between `app.py`'s slow-log `after_request` and `infra/metrics.py`'s `before_request` is implicit. If metrics middleware is ever disabled or unregistered, the `hasattr` guard silently returns False and slow-log goes dark. Not a bug today, but a fragility worth revisiting if metrics middleware gains a disable flag.

- [ ] **Step 1:** Read `app.py:380-445` to see the exact before_request/after_request signatures.
- [ ] **Step 2:** Write a test in `tests/test_app.py` that verifies: a slow request (monkeypatched `time.time`) causes `logger.warning` to be called with the expected `%s`-formatted slow-request message. Run → fail (because path is dead).
- [ ] **Step 3:** Inside `before_request`, add at the very top (before any short-circuit returns that use start_time):

```python
g.start_time = time.time()
```

Ensure `time` is already imported at module top.
- [ ] **Step 4:** Keep the `hasattr` guard as-is (defensive for teardown before before_request runs). Run tests → green.
- [ ] **Step 5:** Suggested commit: `fix(app): set g.start_time in before_request so slow-log path fires`

---

### Task 1.2: Skip correlation-ID log spam for static/health/metrics paths

**Files:** `middleware.py`

**Findings:** `middleware.py:22` logs every request including `/static`, `/health`, `/metrics`.

- [ ] **Step 1:** Read `middleware.py` in full and find where `_SKIP_PATHS` is defined (likely `app.py`).
- [ ] **Step 2:** Write a test asserting that a request to `/metrics` does NOT produce the "New request received" log line, while a request to `/` does.
- [ ] **Step 3:** Import/duplicate the skip prefixes into `middleware.py` (or expose `_SKIP_PATHS` from `app.py`). Gate the `logger.info("New request received. ...", ...)` call on `not any(request.path.startswith(p) for p in SKIP_PATHS)`. Correlation ID generation itself (setting `g.correlation_id`) must STILL run for skipped paths — only the log line is suppressed.
- [ ] **Step 4:** Tests green.
- [ ] **Step 5:** Commit: `fix(middleware): suppress correlation log for static/health/metrics paths`

---

### Task 1.3: Drop query-param TMDb auth fallback (Bearer-only)

**Files:** `movies/tmdb_client.py:201-213`, `tests/test_tmdb_client.py`

**Findings:** Fallback to `api_key` query param for non-JWT keys violates CLAUDE.md "auth via Bearer header".

- [ ] **Step 1:** Read `movies/tmdb_client.py:180-225` to understand `_build_request_options` and its callers. Read `tests/test_tmdb_client.py` for existing coverage.
- [ ] **Step 2:** Write a failing test: `_build_request_options(path, params)` produces an `Authorization: Bearer <key>` header regardless of whether the key contains dots (v3) or not (v4 JWT). `params` must NOT contain `api_key`.
- [ ] **Step 3:** In `_build_request_options`, unconditionally set the Bearer header and never add `api_key` to `request_params`. Delete `_uses_bearer_auth` helper.
- [ ] **Step 4:** Update any existing test that asserted the v3 fallback (replace with the Bearer assertion).
- [ ] **Step 5:** Run `pytest tests/test_tmdb_client.py -v` → green.
- [ ] **Step 6:** Commit: `fix(tmdb): always send Authorization Bearer (no api_key query-param fallback)`

---

### Task 1.4: Fix `infra/cache.py:223` deprecated event-loop access

**Files:** `infra/cache.py`

**Findings:** `asyncio.get_event_loop().create_future()` deprecated in 3.12+.

- [ ] **Step 1:** Read `infra/cache.py:200-260` for context around the call site.
- [ ] **Step 2:** Replace `asyncio.get_event_loop().create_future()` with `asyncio.get_running_loop().create_future()`. This is only reachable from inside an `async def`, so `get_running_loop()` is safe.
- [ ] **Step 3:** Run `pytest tests/test_cache.py -v` to confirm nothing regressed.
- [ ] **Step 4:** Commit: `fix(cache): use get_running_loop() for single-flight futures`

---

### Task 1.5: Fix or remove broken atexit handler in `infra/pool.py`

**Files:** `infra/pool.py:497-516`

**Findings:** `_cleanup_pool_sync` calls `loop.run_until_complete` on the running loop → `RuntimeError`.

- [ ] **Step 1:** Read `infra/pool.py:490-530` and confirm that Quart's shutdown hook already calls `close_pool()` during normal app teardown.
- [ ] **Step 2:** Write a test that imports the function and, with a mocked running loop, asserts the handler does not raise (it should log and return, not call `run_until_complete`).
- [ ] **Step 3:** Rewrite `_cleanup_pool_sync` to: (a) try to get the running loop; if one exists, just log and return (Quart will clean up); (b) otherwise `asyncio.run(close_pool())`.
- [ ] **Step 4:** Tests green.
- [ ] **Step 5:** Commit: `fix(pool): atexit handler no longer crashes on running loop`

---

## Phase 2 — Verified dead code removal

### Task 2.1: Remove truly-dead items

**Files:**
- `session/keys.py` — delete lines 9-13 (`SESSION_TOKEN_KEY`, `SESSION_FINGERPRINT_KEY`, `SESSION_CREATED_KEY`, `SESSION_LAST_ACTIVITY_KEY`, `SESSION_ROTATION_COUNT_KEY`) and the tombstone comment at 24-26.
- `routes.py:175-177` — delete `_validate_csrf_from_form = validate_csrf` alias (grep confirmed zero callers).
- `movies/watched_store.py:136-144` — delete `list_all_watched` (zero callers).
- `app.py:229, 274` — delete the `import os as _os` re-imports; use the module-level `os` instead.
- `logging_config.py:297-298` — delete unused module-level `logger` and the preceding comment.
- `infra/pool.py:108-109` — delete unused `recent_queries` deque plus any writes to it.
- `infra/pool.py:96-107` — delete `rate_limit_hits`, `user_connection_counts`, `ip_connection_counts` entries from the metrics dict and any `get_pool_status` fields that reference them.
- `infra/pool.py:215, 276` — delete the `del user_id, ip_address` no-op lines.
- `infra/pool.py:232-236, 260-263` — delete the `ConnectionMetadata` tracking (dict populate + dead finally writes). Remove the `ConnectionMetadata` dataclass if nothing else uses it.
- `infra/pool.py:434-441` — delete `get_async_connection()` and `release_async_connection()` (`NotImplementedError` stub + no-op).
- `infra/pool.py:122-141` — delete backward-compat CB proxy properties/setters (`circuit_breaker_state`, `circuit_breaker_failures`, `_cb_lock`). **Verify with grep that no tests read these first** — if tests do, update them to use `self._circuit_breaker.state` / `.failures` / `.lock`.
- `infra/secrets.py:16-25` — delete unused `OPTIONAL_SECRETS`.
- `infra/secrets.py:67-80` — delete commented-out `_get_from_secret_manager` body; leave a 1-line docstring stub or delete the function entirely if nothing references it.
- `infra/ssl.py:107,122` — delete the ignored `verify_mode` parameter.
- `movies/query_builder.py:258` + `movies/candidate_store.py:130,165` — delete unused `seed` parameter.
- `movies/candidate_store.py:35-43` — delete unused `excluded_tconsts` parameter from `_filter_cache_key`.
- `movies/tmdb_client.py:176` — delete the class-level `_rate_semaphore = _rate_semaphore` re-bind.

**Findings:** All "dead code" items from the audit **except** the ones grep verified still have callers/tests.

- [ ] **Step 1:** Run `grep -rn SESSION_TOKEN_KEY tests/` to re-confirm zero callers.
- [ ] **Step 2:** For each file above, make one focused Edit. Commit per-file so a regression can be bisected:
  - `chore: drop unused session key constants`
  - `chore(routes): remove unused validate_csrf alias`
  - `chore(watched): remove unused list_all_watched wrapper`
  - `chore(app): drop redundant os re-imports`
  - `chore(logging): remove unused module logger`
  - `chore(pool): drop dead metrics fields and recent_queries`
  - `chore(pool): drop dead user_id/ip_address params`
  - `chore(pool): drop unused ConnectionMetadata tracking`
  - `chore(pool): remove NotImplementedError async stubs`
  - `chore(pool): drop backward-compat CB proxies`
  - `chore(secrets): drop unused OPTIONAL_SECRETS and boto3 placeholder`
  - `chore(ssl): drop ignored verify_mode parameter`
  - `chore(query_builder): drop unused seed parameter`
  - `chore(candidate_store): drop unused excluded_tconsts from cache-key helper`
  - `chore(tmdb): remove no-op class attr rebind`
- [ ] **Step 3:** Run full test suite after each commit. Must stay green.

### Task 2.2: Items flagged as "likely dead" that grep proved LIVE — KEEP

Document in the plan so nobody re-deletes them:

- `movie_service.py:212-226` `render_movie_by_tconst` — tests at `test_app.py:102,142`, `test_movie_service_extended.py:110-132`, `test_routes_navigation.py:20` reference it.
- `movie_service.py:274-285` `filtered_movie` — test at `test_movie_service.py:100`.
- `movie_service.py:286-296` `logout` — tests at `test_movie_service_extended.py:165,177`.
- `movie_renderer.py` — used by `movie_service.py:30,52`; NOT dead.
- `movies/tmdb_client.py:401-442` parse_* pass-throughs — callers in `movies/movie.py`; NOT dead.
- `movies/tmdb_client.py:481` `get_movie_info_by_tmdb_id` — caller in `scripts/update_languages_from_tmdb.py`.
- `movies/tmdb_client.py:487-503` backdrop helpers — tests at `test_tmdb_client.py:34,43`.
- `settings.py:40-41` re-exports — used by 4 scripts (`update_languages_from_tmdb.py`, `check_2024_movies.py`, `add_movie_slugs.py`, `update_ratings.py`).
- `infra/client_ip.py:48` `trusted_proxies` — tests at `test_client_ip.py:14-46`.
- `infra/runtime_schema.py:303` `ensure_movie_candidates_fulltext_index` — called from `app.py:513`.

**No task action; this subsection is intentional documentation so a future auditor doesn't repeat the mistake.**

---

## Phase 3 — Leaky abstraction fixes (add public API, keep private as aliases)

The general pattern for every task in this phase: promote the `_private` name to a public name, keep the `_private` as an alias so tests that reach into privates still work, and update the offending caller(s) to use the public name. This guarantees zero behavior change.

### Task 3.1: Promote `ProjectionStore` enrichment-worker persistence API

**Files:** `movies/projection_store.py`, `movies/projection_enrichment.py`

**Findings:** Enrichment coordinator pokes `store._mark_attempt`, `_select_row`, `_upsert_ready`, `_upsert_failed`. Also `routes.py:99` calls `store._select_row`.

- [ ] **Step 1:** In `projection_store.py`, add public methods `mark_attempt`, `select_row`, `upsert_ready`, `upsert_failed` that simply call the existing private versions. Keep the privates.
- [ ] **Step 2:** Update `movies/projection_enrichment.py` lines 243, 251, 314, 337-338 to call the public names.
- [ ] **Step 3:** Update `routes.py:99` to call `store.select_row(tconst)`.
- [ ] **Step 4:** Run full test suite. Existing tests that patched `_select_row` still work (the private exists).
- [ ] **Step 5:** Commit: `refactor(projection_store): promote enrichment-worker API to public`

### Task 3.2: Add public `attach_cache` to stores

**Files:** `movies/watched_store.py`, `movies/projection_store.py`, `movies/candidate_store.py`, `infra/navigation_state.py`, `movie_service.py`

**Findings:** `movie_service.py:100-107` pokes `_cache` on four stores.

- [ ] **Step 1:** Each store defines `def attach_cache(self, cache): self._cache = cache`.
- [ ] **Step 2:** `movie_service.py:100-107` switches to `.attach_cache(cache)` on each store.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(stores): add attach_cache public setter`

### Task 3.3: Add public facades for `prev_stack_length` and other navigator reach-ins

**Files:** `movie_service.py`, `routes.py:455`

**Findings:** `routes.py:455` uses `movie_manager._navigator.prev_stack_length(state)`.

- [ ] **Step 1:** In `movie_service.py`, add `def prev_stack_length(self, state): return self._navigator.prev_stack_length(state) if self._navigator and state else 0`.
- [ ] **Step 2:** `routes.py:455` switches to `movie_manager.prev_stack_length(state)`.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(movie_service): facade prev_stack_length on navigator`

### Task 3.4: Expose `PoolCircuitBreaker.lock` public, stop reaching into `_lock`

**Files:** `infra/pool_monitors.py`, `infra/pool.py`

**Findings:** `pool.py:140-141` `_cb_lock` proxy reaches `PoolCircuitBreaker._lock`.

- [ ] **Step 1:** Add `@property def lock(self): return self._lock` on `PoolCircuitBreaker`.
- [ ] **Step 2:** Any reader (including tests) can use `.lock` instead. Keep `_lock` attribute — do NOT rename.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(pool): expose circuit breaker lock as public property`

### Task 3.5: Add `update_state_from_usage` on `DatabaseConnectionPool`

**Files:** `infra/pool.py`, `infra/pool_monitors.py`

**Findings:** Monitors mutate `self._pool.state/metrics/connections` directly.

- [ ] **Step 1:** On `DatabaseConnectionPool`, add `update_state_from_usage(free, size)` that encapsulates the thresholds and sets `self.state` **only on transition** (guards the change-detection finding 5 from efficiency below). Return the new state.
- [ ] **Step 2:** `pool_monitors.py:109-127` calls the new method instead of mutating directly.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(pool): encapsulate state transition in update_state_from_usage`

### Task 3.6: Use public `ipaddress` types in `infra/client_ip.py`

**Files:** `infra/client_ip.py:16-35`

**Findings:** Uses `ipaddress._BaseNetwork`.

- [ ] **Step 1:** Replace with `Union[ipaddress.IPv4Network, ipaddress.IPv6Network]`.
- [ ] **Step 2:** Run `mypy infra/client_ip.py --ignore-missing-imports` to confirm the type checks still pass.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(client_ip): use public ipaddress types`

### Task 3.7: `session/quart_session_compat.py:77` defensive guard

**Files:** `session/quart_session_compat.py`

**Findings:** Reaches into `session_interface._config` — upstream internal.

- [ ] **Step 1:** Add `getattr(session_interface, "_config", None)` with an `if` guard and fall back to a safe default. Do NOT change upstream; the shim already exists because quart-session 3.0.0 needs it.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `refactor(session_compat): defensive guard for upstream private access`

---

## Phase 4 — Reuse / duplication consolidation

### Task 4.1: Replace hand-rolled env parsing with `env_int`/`env_float`

**Files (modify call sites, import from `infra.time_utils`):**
- `infra/pool.py:377-380, 388-397` (VALIDATE_SSL bool, 6× ints, 1× float)
- `infra/legacy_migration.py:39, 43, 84`
- `infra/navigation_state.py:56, 60`
- `infra/worker_metrics.py:28-53`
- `config/database.py:27, 35`
- `config/session.py:26-27`
- `worker.py:270-274`
- `app.py:82, 135`

**Findings:** 15+ hand-rolled sites.

- [ ] **Step 1:** One file at a time. For each, `from infra.time_utils import env_int, env_float, env_bool` and replace the hand-rolled `int(os.getenv(...))` with `env_int(name, default)`. **Preserve defaults exactly** — double-check every one.
- [ ] **Step 2:** Run tests after each file.
- [ ] **Step 3:** Commits per file: `refactor(<file>): use env_int/env_float helpers`

### Task 4.2: Consolidate SSL context construction

**Files:** `infra/pool.py:182-195`, `infra/ssl.py`

**Findings:** SSL context verbatim duplication.

- [ ] **Step 1:** Replace `SecureConnectionPool._create_ssl_context` body with `return build_mysql_ssl_context(self.ssl_cert_path)`.
- [ ] **Step 2:** Run `pytest tests/test_pool.py tests/test_ssl.py -v` → green.
- [ ] **Step 3:** Commit: `refactor(pool): use build_mysql_ssl_context from infra.ssl`

### Task 4.3: Factor `runtime_schema.py` `ensure_*` helpers

**Files:** `infra/runtime_schema.py`

**Findings:** 6 helpers each SELECT-then-CREATE with copy-paste.

- [ ] **Step 1:** Add private helpers `_ensure_index(pool, table, name, create_sql)` and `_ensure_column(pool, table, name, create_sql)` that catch the specific MySQL duplicate-index/duplicate-column errors and no-op. This replaces the TOCTOU `SELECT 1 FROM information_schema ... LIMIT 1` pattern.
- [ ] **Step 2:** Refactor each of the 6 `ensure_*_column/index` functions to a single call of the helper. Keep the public function names and signatures identical so `app.py` and tests don't change.
- [ ] **Step 3:** Run `pytest tests/test_runtime_schema.py -v` → green.
- [ ] **Step 4:** Commit: `refactor(runtime_schema): factor ensure_index/ensure_column helpers`

### Task 4.4: Replace triple cache wrappers with `safe_get_or_set`

**Files:** `movies/query_builder.py:277-297`, `movies/candidate_store.py:220-237`, `movies/watched_store.py:26-34,74-103`

- [ ] **Step 1:** Each caller switches to `await cache.safe_get_or_set(namespace, key, loader, ttl)`. Preserve original TTLs and namespaces exactly.
- [ ] **Step 2:** Run full suite — watched-list, navigator, and filter flows must be untouched.
- [ ] **Step 3:** Commit per file.

### Task 4.5: Route all metric emits through `safe_emit`

**Files:** `infra/metrics.py:347-349`, `infra/worker_metrics.py:138,147,157`, `movies/projection_enrichment.py:17-24`, `movies/tmdb_client.py:215-250`

- [ ] **Step 1:** Each caller `from infra.metrics_groups import safe_emit` and replaces the local `try/except: pass` wrappers.
- [ ] **Step 2:** Delete the local wrappers.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(metrics): consolidate try/except wrappers via safe_emit`

### Task 4.6: Collapse `query_builder`/`candidate_store` duplication

**Files:** `movies/query_builder.py`, `movies/candidate_store.py`

**Findings:** WHERE-clause builder, genre-clause wrapper, FULLTEXT fallback detector all duplicated.

- [ ] **Step 1:** Move `MovieQueryBuilder.genre_clause(criteria, use_fulltext=False)` as the single source. Delete `candidate_store._genre_clause`.
- [ ] **Step 2:** Hoist `_is_fulltext_index_error` to a module-level helper in `query_builder.py` and import from `candidate_store`.
- [ ] **Step 3:** In `candidate_store._build_candidate_query`, call `MovieQueryBuilder.build_parameters` for the where-clause values rather than re-reading `criteria.get("min_year", 1900)` etc.
- [ ] **Step 4:** Run the filter/navigator end-to-end tests.
- [ ] **Step 5:** Commit: `refactor(movies): consolidate candidate_store into query_builder`

### Task 4.7: Use `ApiConfig.get_tmdb_api_key()` from `tmdb_client.py`

**Files:** `movies/tmdb_client.py:13-24`, `config/api.py`

- [ ] **Step 1:** In `tmdb_client.py`, replace `get_tmdb_api_key` local helper with `from config.api import ApiConfig` and `return ApiConfig.get_tmdb_api_key()`.
- [ ] **Step 2:** Delete the local helper.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(tmdb): fetch api key via ApiConfig`

### Task 4.8: Remove `metrics["circuit_breaker_trips"]` mirror; read through breaker

**Files:** `infra/pool.py:96-107,178,251,256`, `infra/pool.py:get_pool_status`

- [ ] **Step 1:** Delete the mirror writes. Change `get_pool_status()` to read `self._circuit_breaker.trips` at call time.
- [ ] **Step 2:** Check that any test reading `metrics["circuit_breaker_trips"]` is updated (should just read the status dict which still has the key populated at read time).
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(pool): single source of truth for circuit_breaker_trips`

### Task 4.9: Extract `_PeriodicTask` base for pool monitors

**Files:** `infra/pool_monitors.py`

**Findings:** `PoolHealthMonitor._run()` and `ConnectionCleanup._run()` share scaffolding.

- [ ] **Step 1:** Add `class _PeriodicTask` with `start()`, `stop()`, abstract `async def _tick(self)`, and the sleep/cancel loop.
- [ ] **Step 2:** Both subclasses override `_tick`. Public signatures of the concrete classes unchanged.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(pool_monitors): extract _PeriodicTask base`

### Task 4.10: Replace ad-hoc LRU in `rate_limit.py` and `metrics.py` with `LruExpiringMap`

**Files:** `infra/rate_limit.py:100-131`, `infra/metrics.py:183-277`

- [ ] **Step 1:** Both switch to `LruExpiringMap(max_keys, ttl_seconds)`. Preserve the existing caps and TTLs exactly.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `refactor(infra): use LruExpiringMap helper`

### Task 4.11: Delete backward-compat alias block in `infra/metrics.py:36-85`

**Files:** `infra/metrics.py`

- [ ] **Step 1:** `grep -rn "from infra.metrics import" --include="*.py"` to find all consumers and verify the flat names are only exposed via the alias block (not on `metrics_groups`). **If any caller outside the file uses the flat names**, keep the aliases — they're not dead. **Only proceed if the grep is clean.**
- [ ] **Step 2:** If dead, delete; otherwise mark this task as "kept for API compat" and skip.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `chore(metrics): drop backward-compat alias block` (if actually deleted).

### Task 4.12: Deduplicate `routes.py` filter no-match + OAuth error branches

**Files:** `routes.py:615-626, 830-872`

- [ ] **Step 1:** Extract module-private helpers `_no_matches_response()` and `_oauth_fail(flash_msg)` in `routes.py`.
- [ ] **Step 2:** Replace the duplicated blocks. Keep response bodies and redirect targets byte-identical.
- [ ] **Step 3:** Existing route tests must pass unchanged.
- [ ] **Step 4:** Commit: `refactor(routes): extract filter no-match and OAuth fail helpers`

### Task 4.13: Unify rate-limit key format across Redis and memory paths

**Files:** `infra/rate_limit.py:76,106`

- [ ] **Step 1:** Add `_build_key(endpoint, ip)` that returns `f"ratelimit:{endpoint}:{ip}"`. Both paths call it.
- [ ] **Step 2:** Re-run `tests/test_rate_limit.py`. If a test asserts the memory-path key without the `ratelimit:` prefix, update the assertion (the in-memory fallback is invisible to users, so this is not a behavior change).
- [ ] **Step 3:** Commit: `refactor(rate_limit): unify key format across backends`

### Task 4.14: Collapse `config/api.py` duplicated secret fetchers

**Files:** `config/api.py:9-20`

- [ ] **Step 1:** Keep ONE entry point (`get_flask_secret_key()`). Re-implement `SECRET_KEY` property to delegate to it so both call surfaces remain.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `refactor(api): single-source FLASK_SECRET_KEY fetch`

### Task 4.15: Resolve `config/database.py` pool constants vs `infra/pool.py` env reads

**Files:** `config/database.py:15-16`, `infra/pool.py:388-397`

- [ ] **Step 1:** Move the defaults to `config/database.py` as class constants. Have `infra/pool.py` read `env_int("POOL_MIN_SIZE", DatabaseConfig.POOL_MIN_SIZE)` etc. Single source of truth.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `refactor(pool): read pool size defaults from DatabaseConfig`

---

## Phase 5 — Efficiency

### Task 5.1: Cache `dual_write_enabled()` with 60s TTL

**Files:** `infra/legacy_migration.py:83-96`, `infra/navigation_state.py:413-436`

**Findings:** 1-2 DB round-trips per request.

- [ ] **Step 1:** Write a test: mock DB pool, call `dual_write_enabled()` 100× within 60s, assert only one SELECT is issued.
- [ ] **Step 2:** Add a module-level `_dual_write_cache: tuple[float, bool] | None = None` plus a 60s `monotonic()` TTL check.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `perf(migration): cache dual_write_enabled with 60s TTL`

### Task 5.2: Memoize serialized fields on `NavigationState`

**Files:** `infra/navigation_state.py:341-499`

**Findings:** `_serialized_state_fields` called twice per save.

- [ ] **Step 1:** Add a `_serialized_cache: dict[str, str] | None = None` attribute on `NavigationState` (or external WeakKey dict). Invalidate on `clone()` / mutation.
- [ ] **Step 2:** `save_state` compares cached vs fresh serialization to build the diff.
- [ ] **Step 3:** Benchmark/log JSON call count in test; should halve.
- [ ] **Step 4:** Commit: `perf(nav_state): memoize serialized field cache`

### Task 5.3: Gate startup backfill/ALTER behind one-shot metadata flag

**Files:** `infra/runtime_schema.py:100-110, 189-203`

**Findings:** `UPDATE shuffle_key` and `ALTER TABLE MODIFY COLUMN` fire on every startup.

- [ ] **Step 1:** Read/write a `runtime_metadata` row `shuffle_key_backfill_done`. Skip the UPDATE + ALTER if set.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(runtime_schema): gate shuffle_key backfill behind done-flag`

### Task 5.4: Remove unconditional legacy-key fallback in `infra/cache.py:161-180`

**Files:** `infra/cache.py`

**Findings:** 2× Redis RTT on every cold miss.

- [ ] **Step 1:** Gate the legacy-key read behind an env flag `CACHE_LEGACY_FALLBACK_ENABLED` (default off) with a comment pointing to a future removal date.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(cache): gate legacy-key fallback behind env flag`

### Task 5.5: Change-detection for pool health state + drop active liveness probe

**Files:** `infra/pool_monitors.py:102-139`

**Findings:** Unconditional state reassignment every 10s + pool slot burned on `SELECT 1`.

- [ ] **Step 1:** Only assign `self._pool.state = new_state` when `new_state != self._pool.state`. Log only on transition. (Already covered partially by Task 3.5 — complete it here.)
- [ ] **Step 2:** Gate the `SELECT 1` active probe behind `POOL_HEALTH_ACTIVE_PROBE_ENABLED` (default false).
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `perf(pool_monitors): change-detection + opt-in active probe`

### Task 5.6: Cache current year in `query_builder.py` + `routes.py` context processor

**Files:** `movies/query_builder.py:91,109,169,322`, `routes.py:167,189`

- [ ] **Step 1:** Add a module-level `_CURRENT_YEAR_CACHE: tuple[float, int] | None` with a 1-hour TTL check, exposed via `_current_year()`.
- [ ] **Step 2:** Replace the 4 sites in `query_builder.py` and the 2 in `routes.py` with calls to the helper.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `perf: cache current year for hot-path query and template render`

### Task 5.7: Compute TMDb auth once in `TMDbHelper.__init__`

**Files:** `movies/tmdb_client.py:176-213`

- [ ] **Step 1:** In `__init__`, compute `self._auth_headers = {"Authorization": f"Bearer {api_key}"}` once. Drop the Bearer decision per call — it's unconditional now (see Task 1.3).
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(tmdb): precompute auth headers at init`

### Task 5.8: Add batched `fetch_refs`/`fetch_renderable_payloads`

**Files:** `movies/candidate_store.py`, `movies/projection_store.py`, callers in `movies/movie_navigator.py` / `routes.py` (where per-tconst loops exist)

**Findings:** N+1 patterns.

- [ ] **Step 1:** Add `async def fetch_refs(self, tconsts: list[str])` to `CandidateStore` using `WHERE tconst IN (%s, ..., %s)`. Add `async def fetch_renderable_payloads(self, tconsts: list[str])` to `ProjectionStore`.
- [ ] **Step 2:** Keep the per-tconst methods. Add the batched ones as new public methods.
- [ ] **Step 3:** Identify the hottest caller (navigator queue refill) and switch it to the batched call.
- [ ] **Step 4:** Performance test: navigator refill with 20 tconsts should issue 1 SELECT, not 20.
- [ ] **Step 5:** Commit: `perf(stores): add batched fetch_refs/fetch_renderable_payloads`

### Task 5.9: Pipeline `_current_count_generation` + `_get_cached_count`

**Files:** `movies/query_builder.py:449-450`

- [ ] **Step 1:** Embed generation inside the cached count value so both are fetched in one Redis GET, or use `MGET`. Keep the generation semantics identical.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(query_builder): collapse count+generation into one redis roundtrip`

### Task 5.10: Add LRU cap to `_count_locks`

**Files:** `movies/query_builder.py:272`

- [ ] **Step 1:** Replace raw dict with `LruExpiringMap(512, 300)` or a bounded `OrderedDict`.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(query_builder): bound per-key count lock map`

### Task 5.11: Split `_select_row` into `_for_render` and `_for_enrich` variants

**Files:** `movies/projection_store.py:92-102`

- [ ] **Step 1:** `_select_row_for_render` omits `last_error`/`attempt_count`. `_for_enrich` keeps them. Public `select_row` (from Task 3.1) defaults to the smaller variant.
- [ ] **Step 2:** Enrichment worker uses `_for_enrich`.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `perf(projection_store): split _select_row by use case`

### Task 5.12: Tighten `watched_store.list_watched` column list

**Files:** `movies/watched_store.py:120-128`

- [ ] **Step 1:** Audit which fields the watched-list template actually uses. Drop `p.payload_json` from the SELECT if unused.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(watched): avoid fetching payload_json when unused`

### Task 5.13: Skip unchanged-payload upsert in `enrich_projection`

**Files:** `movies/projection_enrichment.py:334`

- [ ] **Step 1:** Compute `sha256(json.dumps(payload, sort_keys=True))`, compare to stored hash column (add column via a `runtime_schema` helper if absent), skip UPDATE when unchanged.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(enrichment): skip upsert when payload hash unchanged`

### Task 5.14: Hoist per-request env reads and in-function imports

**Files:**
- `app.py:82` — `SLOW_LOG_SAMPLE_RATE` at module load.
- `routes.py:183-193` — `oauth_config` cached on `g` in `before_request`.
- `infra/security_headers.py:19` — hoist `get_environment` import; precompute `_BASELINE_HEADERS`/`_PROD_HEADERS` module dicts.
- `movie_service.py:181` — `PREWARM_TIMEOUT_SECONDS` at module load.
- `app.py:421, 486, 490` — `quart`/`werkzeug` imports hoisted.
- `routes.py:103-104, 207, 240, 340, 794, 814, 816, 834` — all in-function imports hoisted except `metrics_endpoint` (circular-import guard — keep it in-function and document).
- `movie_service.py:114` — `NavigationStateStore` hoisted.
- `worker.py:177` — `bump_count_cache_generation` hoisted.
- `infra/worker_metrics.py:130-133,172-177` — hoisted.

- [ ] **Step 1:** One file at a time. Keep the `metrics_endpoint` lazy import documented.
- [ ] **Step 2:** Commits per file: `refactor(<file>): hoist imports and cache env reads`

### Task 5.15: Bucket HTTP status code labels in metrics

**Files:** `infra/metrics.py:331`

- [ ] **Step 1:** Replace `str(response.status_code)` with `bucket_http_status(response.status_code)` (helper at `metrics.py:140`).
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(metrics): bucket HTTP status label to cap cardinality`

### Task 5.16: Widen optimistic-lock jitter to full jitter

**Files:** `infra/navigation_state.py:543-545`

- [ ] **Step 1:** Replace `random.randint(0, 10)` with `random.randint(0, backoff_ms)`.
- [ ] **Step 2:** Existing retry test should still pass (jitter is randomized, assertions should only check retry count + backoff ordering).
- [ ] **Step 3:** Commit: `perf(nav_state): widen optimistic-lock jitter to full backoff`

---

## Phase 6 — Quality / style

### Task 6.1: Stringly-typed → enums

**Files:** `infra/pool.py`, `infra/pool_monitors.py`, `movies/projection_store.py`, `session/keys.py`, `infra/metrics.py`

- [ ] **Task 6.1a:** Add `class CircuitBreakerState(str, Enum)` in `infra/pool.py` (inherit `str` so existing string comparisons continue working). Replace the raw `"closed"|"open"|"half-open"` strings with enum members. Hoist the per-call `state_map` at `infra/metrics.py:250` to module level.
- [ ] **Task 6.1b:** `movies/projection_store.py:39-42` — replace `PROJECTION_READY = ProjectionState.READY.value` string re-exports with direct enum usage inside the file. **Keep** the `PROJECTION_*` names as module-level aliases if any test imports them (grep first).
- [ ] **Task 6.1c:** `movies/projection_store.py:261,265` — the raw `'ready', 'stale'` in the SQL CASE is unavoidable in SQL text. Interpolate via f-string using `ProjectionState.READY.value` so the enum remains the single source.
- [ ] **Task 6.1d:** Add `SESSION_OAUTH_STATE_KEY = "oauth_state"` to `session/keys.py`, update `routes.py:798,823` to use it.
- Each as its own commit: `refactor: enum-ify circuit breaker state`, `refactor(projection_store): use ProjectionState enum directly`, `refactor(session): add SESSION_OAUTH_STATE_KEY constant`.

### Task 6.2: Extract magic numbers as module constants

**Files:** `movies/query_builder.py:371` (`LIMIT 500`), `movies/candidate_store.py:175` (overfetch ×3), `movies/candidate_store.py:395-396` (128, 2147483647), `worker.py:229-244` (`LIMIT 1000`), `config/session.py:34` (86400).

- [ ] **Step 1:** Hoist each literal to a module constant with a descriptive name.
- [ ] **Step 2:** `candidate_store.py:395-396` — interpolate the DDL from the Python constants (`SAMPLE_BUCKET_COUNT`, `_MYSQL_INT_MAX`) so they can't drift.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit per file.

### Task 6.3: Stringify no-op comments and narration deletions

**Files:** the list from the audit under "Unnecessary comments to delete (concrete lines)".

- [ ] **Step 1:** Open each file and delete only the lines explicitly listed as narration/what-comments:
  - `app.py:69-73, 249-251, 259-260, 427`
  - `routes.py:434-437 (trim only), 640-642, 654-655`
  - `movies/movie.py:99-101, 122, 138, 150, 156, 197, 204`
  - `movies/tmdb_client.py:139-143 (trim), 401-403, 444-447`
  - `movies/query_builder.py:58-60`
  - `movies/projection_store.py:38`
  - `movie_navigator.py:84-89`
  - `movie_service.py:92-98 (trim caller ref)`
  - `infra/pool_monitors.py:63`
  - `infra/metrics.py:168-171, 183-185 (partial), 225, 243-250, 293-308`
  - `infra/ssl.py:56-96, 139`
  - `worker.py:23-25 (trim), 289-316 (trim)`
- [ ] **Step 2:** Leave every comment flagged as "load-bearing WHY" untouched.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** One commit per file: `docs(<file>): drop narrating comments`.

### Task 6.4: Remove emojis from source strings

**Files:** `logging_config.py:288-292`, `infra/ssl.py:132`

- [ ] **Step 1:** Delete the `✓`/`⚠` characters. Keep the message text.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `style: remove emojis from boot log strings`

### Task 6.5: Extract `fetch_slug_and_ratings` param

**Files:** `movies/movie.py:38`

- [ ] **Step 1:** Remove the `tconst` parameter; use `self.tconst`. Update the single caller.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `refactor(movie): drop redundant tconst parameter`

### Task 6.6: Split `watched_list_page` into helpers

**Files:** `routes.py:631-751`

- [ ] **Step 1:** Extract `_build_watched_stats(rows)`, `_normalize_watched_row(row)`, `_parse_watched_pagination(args)` as private module helpers.
- [ ] **Step 2:** Rendered HTML and response body must be byte-identical (snapshot-compare against the current test fixtures).
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `refactor(routes): extract watched_list_page helpers`

### Task 6.7: Extract header constants + response dicts in `infra/security_headers.py`

**Files:** `infra/security_headers.py`

- [ ] **Step 1:** Hoist `_BASELINE_HEADERS` and `_PROD_HEADERS` as module-level dicts. Hoist the `get_environment` import. Keep the `add_security_headers` signature unchanged.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `perf(security_headers): precompute header dicts`

### Task 6.8: `_safe_referrer` → `infra/route_helpers.py`

**Files:** `routes.py:73-78`, `infra/route_helpers.py`

- [ ] **Step 1:** Move the helper. Import it back in `routes.py`. Keep the private underscore name in the caller's local scope as `_safe_referrer = safe_referrer`.
- [ ] **Step 2:** Tests green.
- [ ] **Step 3:** Commit: `refactor(route_helpers): relocate safe_referrer`

### Task 6.9: Harden `with_timeout` cancellation

**Files:** `infra/route_helpers.py:80-104`

- [ ] **Step 1:** Wrap the inner coroutine in `asyncio.ensure_future`. On `TimeoutError`, `await task` after cancellation so the pool connection is actually released before returning.
- [ ] **Step 2:** Write a test that creates a mock store method which sleeps, wraps it in `with_timeout`, asserts the connection counter is decremented after timeout.
- [ ] **Step 3:** Tests green.
- [ ] **Step 4:** Commit: `fix(route_helpers): await cancelled task in with_timeout`

---

## Phase 7 — CLAUDE.md sync

### Task 7.1: Remove stale EnhancedSessionSecurity reference

**Files:** `CLAUDE.md`

**Findings:** CLAUDE.md claims `EnhancedSessionSecurity` lives in `session/user_auth.py`. Grep shows zero matches.

- [ ] **Step 1:** Re-verify: `grep -rn "EnhancedSessionSecurity" --include="*.py"` returns nothing.
- [ ] **Step 2:** Update the `Session State` section to describe what's actually there: `session/user_auth.py` handles user registration; idle/max-age timeouts are enforced via Quart session cookie config in `config/session.py`.
- [ ] **Step 3:** Update the "Key Patterns" section accordingly. Do NOT remove the 8h/15min claim unless the config doesn't set it.
- [ ] **Step 4:** Commit: `docs(CLAUDE.md): sync session security description with actual code`

### Task 7.2: Add reminders about `env_int`/`env_float`, `safe_emit`, `safe_get_or_set`, `build_mysql_ssl_context`, `LruExpiringMap`

**Files:** `CLAUDE.md` "Key Patterns" section

- [ ] **Step 1:** Add a short "Shared helpers" subsection listing the 5 new helpers and where they live.
- [ ] **Step 2:** Commit: `docs(CLAUDE.md): document new shared helpers`

### Task 7.3: Document `runtime_schema` one-shot backfill flag

**Files:** `CLAUDE.md` gotchas section

- [ ] **Step 1:** Add a gotcha line: "Runtime schema: `shuffle_key` backfill is gated on a `runtime_metadata` flag after first run — don't delete the flag unless you intend to re-run the backfill."
- [ ] **Step 2:** Commit: `docs(CLAUDE.md): note runtime_schema backfill gate`

---

## Final Verification (run after Phase 7 is complete)

- [ ] **V1:** `python3 -m pytest tests/ -v` — all tests pass, no new failures vs pre-plan baseline.
- [ ] **V2:** `python3 -m pytest tests/ --cov=. --cov-report=term-missing` — coverage ≥ 40% (CI gate).
- [ ] **V3:** `black . --line-length 100 --check` — clean.
- [ ] **V4:** `flake8 . --exclude=venv,node_modules` — clean.
- [ ] **V5:** `mypy . --ignore-missing-imports` — no new errors.
- [ ] **V6:** CLAUDE.md invariant greps:
  - `grep -rn 'f".*{.*}.*"' --include="*.py" | grep -iE 'logger\.(info|debug|warning|error)'` — empty
  - `grep -rn 'os.getenv.*NEXTREEL_ENV' --include="*.py" | grep -v -E 'config/env.py|env_bootstrap.py'` — empty
  - `grep -rn 'CERT_NONE' --include="*.py"` — empty
  - `grep -rn 'int(os.getenv' --include="*.py" | grep -v tests/` — should be dramatically shorter than before (only remaining sites are intentional exceptions you'll document)
- [ ] **V7:** Spot-check end-to-end workflow:
  - `python3 app.py` → open `http://127.0.0.1:5000/`
  - Click "Pick a Movie" → `/next_movie` → movie detail renders
  - `/previous_movie` works
  - `/filters` → apply filter → movie detail renders
  - `/watched` page loads
  - `/metrics` returns 200 (or 401 if OPS_AUTH_TOKEN set)
  - No `ERROR` or `CRITICAL` lines in stdout during the walkthrough
- [ ] **V8:** `arq worker.WorkerSettings` — starts cleanly, processes one job, exits on SIGINT.
- [ ] **V9:** Leave uncommitted diffs to the user (no autocommit, per user feedback memory).

---

## Out of scope (intentional — not deferred, actively refused)

- **Renaming any public function, class, or module** — too much test/caller churn, no user benefit.
- **Changing any database column or schema value** beyond the shuffle_key backfill gate.
- **Changing response bodies, HTTP status codes, redirect targets, or template HTML.**
- **Modifying session cookie name, lifetime, or cookie-security attributes.**
- **Touching `.env*` files** — hook-blocked.
- **Rewriting `projection_store`/`projection_enrichment` into a single class** — tempting, but the split is load-bearing for worker/web boundary. Phase 3 adds public API instead.
- **Removing the quart-session compat shim** — upstream hasn't shipped a fix.
- **Migrating off the Redis→MySQL navigation dual-write** — that's a separate operational runbook, not a refactor.

## Not a task: findings that the audit flagged but verification proved false positives

Documented in Task 2.2 so future audits don't re-flag them.

---

**End of plan.**
