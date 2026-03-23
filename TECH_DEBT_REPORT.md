# NextReel-Lite — Fresh Tech Debt Audit

**Date:** 2026-03-22
**Method:** Full read of every non-trivial Python file, all templates, CI config, and dependency files.
**Scoring:** Priority = (Impact + Risk) × (6 − Effort). Higher = fix sooner.

---

## Critical — Fix Immediately

### 1. Every POST Form Is Missing Its CSRF Token
**Score: 50** | Impact 5 | Risk 5 | Effort 1

`routes.py:97-100` injects `csrf_token()` into template context via `@bp.app_context_processor`. But **zero templates actually use it.** Verified:

- `set_filters.html:86` — `<form action="/filtered_movie" method="post">` — no hidden `csrf_token` field
- `movie_card.html:308,314,372,377` — four `<form method="POST">` elements — none include a CSRF field

Meanwhile, `_validate_csrf_from_form()` at `routes.py:80-94` checks both the `X-CSRFToken` header and `csrf_token` form field. Since neither exists in the HTML, **every form submission should be rejected with 403** — unless the CSRF check silently passes (which suggests sessions might not have a token stored yet, meaning `expected` is None and `abort()` fires).

The fact that the app works at all means either: (a) the session always has a CSRF token because some other request path generates it, or (b) there's an untested bypass. Either way, this is broken.

**Fix:** Add `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` to every POST form. Verify with a test.

---

### 2. 1,273 `node_modules` Files Tracked in Git
**Score: 45** | Impact 5 | Risk 4 | Effort 1

`node_modules/` is **not in `.gitignore`** and 1,273 files are committed. This bloats the repo, slows clones, and is a well-known anti-pattern. The `package.json` only has `tailwindcss` as a devDependency — this is purely a CSS build tool.

**Fix:** `echo 'node_modules/' >> .gitignore && git rm -r --cached node_modules && git commit -m "Remove tracked node_modules"`.

---

### 3. `_validate_csrf()` Is Dead Code With a Silent Pass-Through
**Score: 40** | Impact 5 | Risk 5 | Effort 2

`routes.py:62-77` — the synchronous `_validate_csrf()` function is **never called by any route**. All routes use `_validate_csrf_from_form()` instead. But `_validate_csrf()` also has a logic bug: if the header token is `None` (line 72), it `pass`es, then falls through to line 75 which checks `if submitted and ...` — since `submitted` is `None`, the condition is False, and the function **returns without aborting**. This is a CSRF bypass if anyone ever calls it.

**Fix:** Delete `_validate_csrf()` entirely. It's unused and broken.

---

### 4. `movie_queue_size` Gauge Uses `user_id` Label — Cardinality Bomb
**Score: 36** | Impact 4 | Risk 5 | Effort 3

`metrics_collector.py:90-94`:
```python
movie_queue_size = Gauge(
    'nextreel_movie_queue_size',
    'Current movie queue size',
    ['user_id']
)
```

Every unique user creates a new time series in Prometheus. With anonymous sessions generating UUIDs, this is unbounded. Prometheus will OOM or Grafana Cloud will rate-limit your scrapes.

**Fix:** Remove the `user_id` label. Use a summary/histogram of queue sizes instead, or track only aggregate min/max/avg.

---

## High — Fix This Sprint

### 5. `SimpleCacheManager` Opens Its Own Redis Connection Outside the Shared Pool
**Score: 32** | Impact 4 | Risk 4 | Effort 2

`app.py:80-87` creates a shared `ConnectionPool`. `app.py:96` then passes the Redis URL to `SimpleCacheManager`, which creates a **second, independent connection** in `simple_cache.py:44`. Two connections to Upstash in production when one pool should serve both.

**Fix:** Accept a `ConnectionPool` or `Redis` instance in `SimpleCacheManager.__init__()` instead of a URL.

---

### 6. `form_data.to_dict()` Drops Multi-Value Filter Selections
**Score: 30** | Impact 5 | Risk 4 | Effort 3

`routes.py:359` — `session[CURRENT_FILTERS_KEY] = form_data.to_dict()`. Quart's `MultiDict.to_dict()` keeps only the **last value** for each key. If a user selects multiple genres (which the filter form supports via checkboxes), only the last genre survives. This silently breaks multi-genre filtering.

**Fix:** Use `form_data.to_dict(flat=False)` or handle multi-valued keys explicitly in `extract_movie_filter_criteria()`.

---

### 7. Shared Mutable Default in `session_auth.py`
**Score: 28** | Impact 4 | Risk 4 | Effort 2

`session_auth.py:25-30`:
```python
DEFAULT_CRITERIA = {
    "min_year": 1900,
    "max_year": datetime.now().year,
    "min_rating": 7.0,
    "genres": ["Action", "Comedy"],
}
```

This dict is created once at import time. `session_auth.py:71` passes it as a default: `session.get(CRITERIA_KEY, DEFAULT_CRITERIA)`. If any downstream code mutates the returned dict (e.g., `criteria["genres"].append("Drama")`), **it mutates the module-level default for all future users**.

Also, `max_year` is frozen to the year the process started. A process running across midnight on New Year's Eve will serve stale year limits until restart.

**Fix:** Return a fresh copy: `session.get(CRITERIA_KEY) or {**DEFAULT_CRITERIA, "genres": list(DEFAULT_CRITERIA["genres"])}`.

---

### 8. Hardcoded `2024` Cache Routing in `filter_backend.py`
**Score: 28** | Impact 4 | Risk 3 | Effort 3

`filter_backend.py:37` — `return min_year >= 2024 or (min_year < 2024 and max_year >= 2024)` determines whether queries use `recent_movies_cache`. This threshold is frozen. As time passes, "recent" stays stuck at 2024.

**Fix:** `threshold = datetime.now().year - 2` or make it configurable.

---

### 9. Dead Code Still in Version Control (13+ Files)
**Score: 25** | Impact 3 | Risk 2 | Effort 1

Files that exist only to `raise ImportError`:

| Location | Count |
|----------|-------|
| `session_auth_enhanced.py`, `backup_manager.py`, `setup_production_env.py`, `performance_comparison.py` | 4 |
| Root-level test stubs (`test_*.py`, `*_test.py` at root) | 10 |
| `scripts/movie_queue.py` (507 lines, only imported by one test) | 1 |

Plus 8 test files in `tests/` that are `pytest.skip()` at module level — they never run.

**Fix:** `git rm` the stub files. Delete or rewrite the skipped tests.

---

### 10. `secrets_manager.py` Logs Secrets With f-strings
**Score: 24** | Impact 3 | Risk 5 | Effort 2

`secrets_manager.py:108`:
```python
logger.info(f"Secret '{key}' validated: {masked}")
```

Two issues: (a) f-string in a log call bypasses lazy evaluation — the masking runs even if INFO logging is disabled; (b) if the secret is exactly 8 chars, the masking logic at line 107 produces `value[:4] + '' + value[-4:]` which is the **full secret with no masking**.

**Fix:** Use `%s` formatting. Fix the masking edge case for `len(value) <= 8`.

---

## Medium — Fix Within 2 Sprints

### 11. Dev Dependencies Mixed With Production in `requirements.txt`
**Score: 20** | Impact 3 | Risk 3 | Effort 2

pytest, black, mypy, pylint, sphinx, flake8 are all in the same `requirements.txt` as production deps. This bloats Docker images and increases attack surface in production.

**Fix:** Split into `requirements.txt` (prod) and `requirements-dev.txt` (dev/test).

---

### 12. `FLASK_ENV` Used for Security Decisions — Deprecated Variable
**Score: 18** | Impact 2 | Risk 3 | Effort 3

At least 6 files check `FLASK_ENV` to toggle SSL enforcement, cookie security, HSTS headers, and database credentials. `FLASK_ENV` was deprecated in Flask 2.2. More importantly, it defaults to `"development"` everywhere — meaning **if the env var is unset in production, the app runs with dev-mode security** (no HTTPS cookies, no HSTS, lax SameSite).

**Fix:** Introduce `NEXTREEL_ENV`, default it to `"production"` (fail-secure), grep-replace all `FLASK_ENV` references.

---

### 13. In-Memory Rate Limiter Leaks Memory
**Score: 16** | Impact 3 | Risk 2 | Effort 2

`routes.py:135` — `_rate_limit_store: dict[str, list[float]]` — old IP+endpoint keys are never evicted. The timestamp lists are pruned on access, but keys for IPs that stop visiting accumulate forever.

**Fix:** Use `cachetools.TTLCache` or add a periodic cleanup to APScheduler.

---

### 14. No Coverage Threshold in CI
**Score: 16** | Impact 4 | Risk 4 | Effort 4

CI runs pytest with `--cov=.` and uploads to Codecov, but there's no `--cov-fail-under` gate. Coverage can drop to zero without failing the build. Only 35 test functions exist, 8 of which are `pytest.skip()` placeholders.

**Fix:** Set `--cov-fail-under=50` (or whatever your current baseline is). Remove or rewrite the skipped tests.

---

### 15. `_active_users` Dict in MetricsCollector Grows Unbounded
**Score: 16** | Impact 3 | Risk 3 | Effort 2

`metrics_collector.py` tracks active users in an in-memory dict with no eviction. Combined with UUID-based session IDs, this grows linearly with traffic.

**Fix:** Add TTL-based eviction or use a fixed-size LRU.

---

### 16. `python-publish.yml` Uses Deprecated Actions
**Score: 12** | Impact 2 | Risk 3 | Effort 2

`.github/workflows/python-publish.yml` still uses `actions/checkout@v2` and `actions/setup-python@v2` (deprecated since 2023). The main CI workflow is updated to v4/v5, but this one was missed.

**Fix:** Update to `@v4`/`@v5`.

---

### 17. Genre FULLTEXT Search Doesn't Escape Quotes
**Score: 12** | Impact 3 | Risk 2 | Effort 3

`filter_backend.py:130` — `f'+"{genre}"'` — if a genre name contains a double quote, the FULLTEXT boolean syntax breaks. Not a SQL injection risk (parameterized), but causes silent search failures for genres with special characters.

**Fix:** Escape or strip special FULLTEXT characters from genre names.

---

## Low — Plan for Next Quarter

### 18. `secure_pool.py` / `database/pool.py` Unnecessary Indirection
**Score: 8** | Impact 2 | Risk 1 | Effort 2

`DatabaseConnectionPool` wraps `SecureConnectionPool` which wraps `aiomysql.Pool`. Three layers of abstraction for a connection pool. The "secure" naming is misleading — it's just optional SSL.

---

### 19. Stale Documentation
**Score: 6** | Impact 2 | Risk 1 | Effort 3

`ARCHITECTURE_AUDIT.md`, `ADR-001-ARCHITECTURE-AUDIT.md`, `ACTION_PLAN.md`, `PERFORMANCE_IMPROVEMENTS.md`, and `SYSTEM_DESIGN_REVIEW.md` all reference issues that have since been fixed. They create confusion about the actual state of the codebase.

---

### 20. Old CSS Files in `static/styles/`
**Score: 4** | Impact 1 | Risk 1 | Effort 1

Five CSS files (`navbar.css`, `header.css`, `footer.css`, `circle_progress_bar.css`, `backdrop_gradient.css`) appear unused — the app uses compiled Tailwind CSS. Dead weight.

---

## Summary

| Priority | Count | Est. Effort |
|----------|-------|-------------|
| Critical | 4 | ~3 hours |
| High | 6 | ~2 days |
| Medium | 7 | ~3 days |
| Low | 3 | ~1 day |

**If you do nothing else, do these three:**

1. **Add CSRF tokens to every form** — your POST endpoints either silently fail or silently bypass protection. Both are bad.
2. **Remove node_modules from git** — 1,273 files, one command to fix.
3. **Remove `user_id` from the Prometheus Gauge label** — this will eventually OOM your metrics backend.

**Biggest systemic risk:** The `FLASK_ENV` defaulting to `"development"` means a single missing env var in production disables HTTPS cookies, HSTS, and strict SameSite. This should default to `"production"`.
