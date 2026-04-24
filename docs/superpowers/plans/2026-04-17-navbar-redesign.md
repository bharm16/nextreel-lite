# Navbar Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current thin navbar with a scroll-aware editorial action bar featuring a stacked brand+tagline lockup, a Spotlight-style search modal, promoted Watched link, prominent Pick pill, and updated avatar dropdown — across desktop and mobile.

**Architecture:** One reusable `navbar_modern.html` template (unchanged consumers). Scroll-aware via a small JS listener toggling a CSS modifier class. Search implemented as a new Spotlight-style modal (mirroring the existing filter-drawer overlay pattern) backed by a new `GET /api/search` JSON route and a new `MovieQueryBuilder.search_titles` method. All styling via existing design tokens plus four small additions. No changes to filter drawer, hero arrows, or `home.html`.

**Tech Stack:** Quart, Jinja2, vanilla JS (no new framework), Tailwind CSS v3 (via `input.css` / `output.css`), pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-17-navbar-redesign-design.md`

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `templates/_search_spotlight.html` | Spotlight modal markup — included once by navbar |
| `static/js/navbar-scroll.js` | Scroll listener toggling `.navbar--solid` class (~25 lines) |
| `static/js/search-spotlight.js` | Modal open/close, keyboard bindings, debounced fetch, result keyboard nav (~100 lines) |
| `nextreel/web/routes/search.py` | `GET /api/search` JSON route |
| `tests/test_query_builder_search.py` | Tests for `MovieQueryBuilder.search_titles` |
| `tests/web/test_search_route.py` | Tests for `/api/search` endpoint |

### Modified files

| File | Change |
|------|--------|
| `static/css/tokens.css` | Add `--duration-surface`, `--duration-modal`, `--easing-measured`, `--color-accent-hover` in all three theme blocks (`:root`, `[data-theme="light"]`, `[data-theme="dark"]`) |
| `static/css/input.css` | Restructure `.navbar-*` rules (lines 22-125); add `.navbar-brand-wrap`, `.navbar-tagline`, `.navbar-icon-btn`, `.navbar-pill`, `.navbar--solid`, `.search-spotlight-*`; update `.navbar-mobile-links`, `.account-avatar-dropdown-menu` items |
| `templates/navbar_modern.html` | Restructure: brand-wrap with tagline, ⌕ icon button, promoted Watched, Pick pill (not link), updated avatar dropdown (Account·Theme·Log Out), updated mobile panel, include search spotlight partial + scroll JS + search JS |
| `movies/query_builder.py` | Add `MovieQueryBuilder.search_titles(pool, query, limit=10)` static method |
| `nextreel/web/routes/__init__.py` | Import and re-export new `search_titles` route function |
| `static/css/output.css` | Regenerated via `npm run build-css` |

---

## Task 1: Add Design Tokens

**Files:**
- Modify: `static/css/tokens.css`

- [ ] **Step 1: Read current `tokens.css` to confirm structure**

Run: cat the file to confirm the three theme blocks (`:root` lines 2-35, `[data-theme="light"]` lines 57-71, `[data-theme="dark"]` lines 73-87).

- [ ] **Step 2: Add new tokens to `:root` block**

Find the `:root {` block (starts at line 2). Immediately before the closing `}` at line 35 (which follows `color-scheme: light;`), insert:

```css
  /* New navbar-redesign tokens (2026-04-17) */
  --duration-surface: 250ms;
  --duration-modal: 300ms;
  --easing-measured: ease-in-out;
  --color-accent-hover: #9e5843;
```

Note: `--color-accent-hover: #9e5843` is the light-mode value (deeper terracotta). The dark-mode block will override it.

- [ ] **Step 3: Add `--color-accent-hover` to `[data-theme="light"]` block**

In the `[data-theme="light"]` block (lines 57-71), immediately after the existing `--color-accent: #b0654f;` line, add:

```css
  --color-accent-hover: #9e5843;
```

- [ ] **Step 4: Add `--color-accent-hover` to `[data-theme="dark"]` block**

In the `[data-theme="dark"]` block (lines 73-87), immediately after the existing `--color-accent: #c67a5c;` line, add:

```css
  --color-accent-hover: #b56a4d;
```

- [ ] **Step 5: Add `--color-accent-hover` to the `@media (prefers-color-scheme: dark)` block**

In the `@media (prefers-color-scheme: dark) { :root { ... } }` block (lines 38-54), after the existing `--color-accent: #c67a5c;` line, add:

```css
    --color-accent-hover: #b56a4d;
```

- [ ] **Step 6: Verify**

Run: `grep -n "accent-hover\|duration-surface\|duration-modal\|easing-measured" static/css/tokens.css`

Expected: 4 new tokens in `:root`, `--color-accent-hover` in each of the three theme blocks. Total 7 matches.

- [ ] **Step 7: Commit**

```bash
git add static/css/tokens.css
git commit -m "feat(navbar): add navbar-redesign design tokens"
```

---

## Task 2: Add `MovieQueryBuilder.search_titles` (TDD)

**Files:**
- Modify: `movies/query_builder.py`
- Create: `tests/test_query_builder_search.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_query_builder_search.py`:

```python
"""Tests for MovieQueryBuilder.search_titles — the new title/actor search used by /api/search."""

from __future__ import annotations

import pytest

from movies.query_builder import MovieQueryBuilder


def test_search_titles_rejects_empty_query():
    """An empty query string yields no rows without hitting the DB."""
    query_sql, params = MovieQueryBuilder.build_search_query("", limit=10)
    assert query_sql is None
    assert params is None


def test_search_titles_rejects_single_char_query():
    """A 1-char query is below the minimum length threshold."""
    query_sql, params = MovieQueryBuilder.build_search_query("a", limit=10)
    assert query_sql is None
    assert params is None


def test_search_titles_builds_parameterized_query():
    """A valid query produces parameterized SQL against movie_candidates with %s placeholders."""
    query_sql, params = MovieQueryBuilder.build_search_query("chungking", limit=10)

    assert query_sql is not None
    # Queries the denormalized movie_candidates cache (not movie_projection, which stores JSON)
    assert "movie_candidates" in query_sql
    assert "primaryTitle" in query_sql
    assert "startYear" in query_sql
    # Must use parameterized placeholders — never f-string interpolation for values
    assert "%s" in query_sql
    assert "chungking" not in query_sql.lower()  # value appears only in params
    # Must order by relevance rank then rating
    assert "ORDER BY" in query_sql.upper()
    assert "LIMIT %s" in query_sql

    # Params include the three LIKE patterns (exact/prefix/contains) plus the limit
    assert "chungking" in params[0].lower() or params[0].lower() == "chungking"
    assert params[-1] == 10  # LIMIT bound


def test_search_titles_escapes_sql_wildcards():
    """Queries containing % or _ must be escaped so they're treated as literals."""
    query_sql, params = MovieQueryBuilder.build_search_query("50%", limit=10)

    assert query_sql is not None
    # Escaped wildcards — the % in the user query should be prefixed with \
    assert any(r"50\%" in p or r"\%" in p for p in params if isinstance(p, str))


def test_search_titles_respects_custom_limit():
    """Limit is passed through as the final parameter."""
    _, params = MovieQueryBuilder.build_search_query("drama", limit=5)
    assert params[-1] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_query_builder_search.py -v`
Expected: `AttributeError: type object 'MovieQueryBuilder' has no attribute 'build_search_query'`

- [ ] **Step 3: Implement `build_search_query`**

Open `movies/query_builder.py`. At the end of the `MovieQueryBuilder` class, add:

```python
    @staticmethod
    def build_search_query(raw_query: str, limit: int = 10) -> tuple[str | None, list | None]:
        """Build a parameterized title-search query against `movie_candidates`.

        `movie_candidates` is the denormalized cache table (see
        `infra/runtime_schema.py:136`) populated by `refresh_movie_caches()`.
        It stores `primaryTitle`, `startYear`, and `averageRating` in typed
        columns — perfect for fast LIKE-based title lookup. Director and
        poster data live in `movie_projection.payload_json` and require
        per-movie enrichment; the search UI intentionally omits them.

        Returns (sql, params) — or (None, None) when the query is below the
        minimum length threshold. Callers should short-circuit on None without
        hitting the DB.

        The query ranks results by: (1) exact title match, (2) title starts
        with the term, (3) title contains the term. Within each bucket rows
        are ordered by averageRating desc.

        Wildcards (% and _) in the user query are escaped so they're treated
        as literal characters, not SQL pattern metacharacters.
        """
        cleaned = (raw_query or "").strip()
        if len(cleaned) < 2:
            return None, None

        # Escape SQL LIKE metacharacters. Order matters: escape \ first, then % and _.
        escaped = cleaned.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")

        exact = escaped.lower()
        prefix = f"{escaped}%"
        contains = f"%{escaped}%"

        sql = (
            "SELECT tconst, primaryTitle, startYear, averageRating "
            "FROM movie_candidates "
            "WHERE primaryTitle IS NOT NULL "
            "  AND (LOWER(primaryTitle) = %s "
            "       OR primaryTitle LIKE %s ESCAPE '\\\\' "
            "       OR primaryTitle LIKE %s ESCAPE '\\\\') "
            "ORDER BY "
            "  CASE "
            "    WHEN LOWER(primaryTitle) = %s THEN 0 "
            "    WHEN primaryTitle LIKE %s ESCAPE '\\\\' THEN 1 "
            "    ELSE 2 "
            "  END, "
            "  COALESCE(averageRating, 0) DESC "
            "LIMIT %s"
        )

        params = [exact, prefix, contains, exact, prefix, int(limit)]
        return sql, params
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_query_builder_search.py -v`
Expected: All 5 tests pass.

- [ ] **Step 5: Verify existing tests still pass**

Run: `python3 -m pytest tests/ -v -k "query_builder" --no-header`
Expected: All query-builder tests pass (no regressions).

- [ ] **Step 6: Commit**

```bash
git add movies/query_builder.py tests/test_query_builder_search.py
git commit -m "feat(search): add MovieQueryBuilder.build_search_query for title lookup"
```

---

## Task 3: Add `GET /api/search` Route (TDD)

**Files:**
- Create: `nextreel/web/routes/search.py`
- Modify: `nextreel/web/routes/__init__.py`
- Create: `tests/web/test_search_route.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_search_route.py`:

```python
"""Tests for the /api/search JSON endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_search_route_returns_empty_for_missing_query(test_client):
    response = await test_client.get("/api/search")
    assert response.status_code == 200
    data = await response.get_json()
    assert data == {"results": []}


@pytest.mark.asyncio
async def test_search_route_returns_empty_for_short_query(test_client):
    response = await test_client.get("/api/search?q=a")
    assert response.status_code == 200
    data = await response.get_json()
    assert data == {"results": []}


@pytest.mark.asyncio
async def test_search_route_returns_results_for_valid_query(test_client):
    fake_rows = [
        {
            "tconst": "tt0109424",
            "primaryTitle": "Chungking Express",
            "startYear": 1994,
            "averageRating": 8.1,
        }
    ]
    with patch(
        "nextreel.web.routes.search._execute_search",
        new=AsyncMock(return_value=fake_rows),
    ):
        response = await test_client.get("/api/search?q=chungking")
        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["results"]) == 1
        assert data["results"][0]["tconst"] == "tt0109424"
        assert data["results"][0]["title"] == "Chungking Express"
        assert data["results"][0]["year"] == 1994
        assert data["results"][0]["rating"] == 8.1


@pytest.mark.asyncio
async def test_search_route_handles_database_error_gracefully(test_client):
    with patch(
        "nextreel.web.routes.search._execute_search",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        response = await test_client.get("/api/search?q=drama")
        assert response.status_code == 200  # degrade gracefully — never 500
        data = await response.get_json()
        assert data == {"results": []}
```

Note: `test_client` fixture is provided by the existing `tests/web/conftest.py`. If it doesn't exist at that path, check `tests/conftest.py` — adapt the import if needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_search_route.py -v`
Expected: 404 on every route (search endpoint not registered).

- [ ] **Step 3: Create the route module**

Create `nextreel/web/routes/search.py`:

```python
"""Live movie title search — backs the Spotlight modal in the navbar."""

from __future__ import annotations

from quart import jsonify, request

from infra.route_helpers import rate_limited, with_timeout
from logging_config import get_logger
from movies.query_builder import MovieQueryBuilder
from nextreel.web.routes.shared import _REQUEST_TIMEOUT, _services, bp

logger = get_logger(__name__)

_SEARCH_LIMIT = 10
_MIN_QUERY_LENGTH = 2


async def _execute_search(sql: str, params: list) -> list[dict]:
    """Run the title-search query against the pool. Extracted for test injection."""
    services = _services()
    # Pool is exposed through movie_manager (see shared.py _services() and
    # NextReelServices dataclass; pool is wired as movie_manager.db_pool).
    pool = services.movie_manager.db_pool
    rows = await pool.execute(sql, params, fetch="all")
    return rows or []


@bp.route("/api/search", methods=["GET"])
@rate_limited("search_titles")
@with_timeout(_REQUEST_TIMEOUT)
async def search_titles():
    """Live title search backing the Spotlight modal.

    Degrades gracefully — always returns 200 with a (possibly empty) results
    list, so the frontend UI never renders an error state mid-typing.
    """
    raw_query = request.args.get("q", "").strip()
    sql, params = MovieQueryBuilder.build_search_query(raw_query, limit=_SEARCH_LIMIT)

    if sql is None:
        return jsonify({"results": []})

    try:
        rows = await _execute_search(sql, params)
    except Exception as exc:  # noqa: BLE001 — defense-in-depth
        logger.warning("Search query failed for q=%r: %s", raw_query, exc)
        return jsonify({"results": []})

    results = [
        {
            "tconst": row.get("tconst"),
            "title": row.get("primaryTitle"),
            "year": row.get("startYear"),
            "rating": float(row["averageRating"]) if row.get("averageRating") is not None else None,
        }
        for row in rows
    ]
    return jsonify({"results": results})
```

- [ ] **Step 4: Register the route in `__init__.py`**

Open `nextreel/web/routes/__init__.py`. After the existing `from nextreel.web.routes.ops import ...` line (around line 35), add:

```python
from nextreel.web.routes.search import search_titles
```

Then add `"search_titles",` to the `__all__` list in alphabetical order (between `"register_submit"` and `"remove_from_watched"`).

- [ ] **Step 5: Verify rate-limit integration**

The existing rate limiter in `infra/rate_limit.py` uses global `RATE_LIMIT_WINDOW = 60` seconds and `RATE_LIMIT_MAX = 30` per endpoint_key. There is no per-endpoint bucket registry — each endpoint gets the same limit under its own counter. No config change needed; the `@rate_limited("search_titles")` decorator in the route above automatically uses the global limits.

Confirm by reading `infra/rate_limit.py:20-21` and verifying `RATE_LIMIT_MAX = 30` and `RATE_LIMIT_WINDOW = 60`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/web/test_search_route.py -v`
Expected: All 4 tests pass.

- [ ] **Step 7: Verify app still boots**

Run: `python3 -c "from nextreel.web.app import create_app; import asyncio; app = asyncio.run(create_app())"` — or briefly start the dev server (`python3 app.py`) and hit `curl 'http://127.0.0.1:5000/api/search?q=drama'`.
Expected: Returns `{"results": [...]}` with at most 10 rows (may be empty if `movie_candidates` is not yet populated in the dev DB — run `mysql -e "CALL refresh_movie_caches()"` to seed it if empty).

- [ ] **Step 8: Commit**

```bash
git add nextreel/web/routes/search.py nextreel/web/routes/__init__.py infra/route_helpers.py tests/web/test_search_route.py
git commit -m "feat(search): add GET /api/search JSON endpoint"
```

---

## Task 4: Update CSS — Navbar Base Styles

**Files:**
- Modify: `static/css/input.css`

- [ ] **Step 1: Locate existing navbar rules**

Run: `grep -n '\.navbar\b\|\.navbar-brand\|\.navbar-actions\|\.navbar-link\|\.navbar-btn\|\.navbar-mobile' static/css/input.css | head -30`

Confirm the existing rules live between lines ~22-125.

- [ ] **Step 2: Replace `.navbar` and add `.navbar--solid`**

Find the current `.navbar { ... }` block (starts around line 23). Replace the existing rule with:

```css
  /* ── Navbar — scroll-aware editorial action bar ─────────── */
  .navbar {
    position: absolute; top: 0; left: 0; right: 0;
    z-index: 50;
    display: flex; align-items: center; gap: 16px;
    padding: 18px 26px;
    background: transparent;
    backdrop-filter: blur(5px);
    -webkit-backdrop-filter: blur(5px);
    transition:
      background var(--duration-surface) var(--easing-measured),
      backdrop-filter var(--duration-surface) var(--easing-measured),
      border-color var(--duration-surface) var(--easing-measured);
    border-bottom: 1px solid transparent;
  }
  [data-theme="dark"] .navbar.navbar--solid,
  :root:not([data-theme="light"]) .navbar.navbar--solid {
    background: rgba(17, 17, 17, 0.88);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom-color: var(--color-border);
  }
  [data-theme="light"] .navbar.navbar--solid {
    background: rgba(245, 244, 240, 0.92);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom-color: var(--color-border);
  }
```

- [ ] **Step 3: Replace `.navbar-brand` and add `.navbar-brand-wrap` + `.navbar-tagline`**

Find the existing `.navbar-brand` rule. Replace with:

```css
  .navbar-brand-wrap {
    display: flex;
    flex-direction: column;
    gap: 1px;
    text-decoration: none;
  }
  .navbar-brand {
    font-family: var(--font-serif);
    font-weight: 700;
    font-size: 22px;
    line-height: 1;
    letter-spacing: -0.02em;
    color: #fff;
    text-decoration: none;
  }
  .navbar.navbar--solid .navbar-brand { color: var(--color-text); }
  .navbar-tagline {
    font-family: var(--font-sans);
    font-weight: 500;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.24em;
    color: rgba(255, 255, 255, 0.5);
    margin-top: 4px;
  }
  .navbar.navbar--solid .navbar-tagline { color: var(--color-text-muted); }
```

- [ ] **Step 4: Replace `.navbar-actions` and update `.navbar-link`**

Find `.navbar-actions` and `.navbar-link`. Replace both rules with:

```css
  .navbar-actions {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-left: auto;
  }
  .navbar-link {
    font-family: var(--font-sans);
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.7);
    text-decoration: none;
    transition: color var(--duration-normal) var(--easing-default);
    white-space: nowrap;
  }
  .navbar-link:hover { color: #fff; }
  .navbar.navbar--solid .navbar-link { color: var(--color-text-muted); }
  .navbar.navbar--solid .navbar-link:hover { color: var(--color-text); }
  .navbar-link:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
    border-radius: 2px;
  }
```

- [ ] **Step 5: Add `.navbar-icon-btn` and `.navbar-pill`**

Directly after the `.navbar-link` rules from Step 4, add:

```css
  .navbar-icon-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 3px;
    background: transparent;
    color: rgba(255, 255, 255, 0.8);
    cursor: pointer;
    transition:
      color var(--duration-normal) var(--easing-default),
      border-color var(--duration-normal) var(--easing-default),
      background var(--duration-normal) var(--easing-default);
  }
  .navbar-icon-btn:hover {
    color: #fff;
    border-color: rgba(255, 255, 255, 0.3);
    background: rgba(255, 255, 255, 0.04);
  }
  .navbar-icon-btn:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }
  .navbar-icon-btn[aria-expanded="true"] {
    border-color: rgba(198, 122, 92, 0.5);
    box-shadow: 0 0 0 2px rgba(198, 122, 92, 0.25);
  }
  .navbar.navbar--solid .navbar-icon-btn {
    border-color: var(--color-border);
    color: var(--color-text-muted);
  }
  .navbar.navbar--solid .navbar-icon-btn:hover {
    color: var(--color-text);
    background: color-mix(in srgb, var(--color-text) 4%, transparent);
  }
  .navbar-icon-btn svg {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .navbar-pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 10px 18px;
    border: 0;
    border-radius: 3px;
    background: var(--color-accent);
    color: #fff;
    font-family: var(--font-sans);
    font-size: 11.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    text-decoration: none;
    cursor: pointer;
    box-shadow: 0 2px 10px rgba(198, 122, 92, 0.3);
    transition:
      background var(--duration-normal) var(--easing-default),
      box-shadow var(--duration-normal) var(--easing-default),
      transform var(--duration-fast) var(--easing-default);
  }
  .navbar-pill:hover {
    background: var(--color-accent-hover);
    box-shadow: 0 2px 14px rgba(198, 122, 92, 0.5);
  }
  .navbar-pill:active { transform: scale(0.98); }
  .navbar-pill:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }
  .navbar-pill svg {
    width: 12px;
    height: 12px;
    fill: currentColor;
  }
```

- [ ] **Step 6: Delete obsolete `.navbar-btn` rules**

Find the `.navbar-btn { ... }` and `.navbar-btn:hover { ... }` rules (lines ~52-72 in the original file) and delete them entirely. The legacy class is no longer used.

- [ ] **Step 7: Rebuild Tailwind CSS**

Run: `npm run build-css`
Expected: `output.css` regenerated with the new classes visible.

Verify: `grep -c "navbar-pill\|navbar-icon-btn\|navbar-brand-wrap" static/css/output.css`
Expected: Output shows a positive count.

- [ ] **Step 8: Commit**

```bash
git add static/css/input.css static/css/output.css
git commit -m "feat(navbar): add editorial action bar styles — scroll-aware surface, pill, icon button, tagline"
```

---

## Task 5: Update CSS — Avatar Dropdown + Mobile Panel

**Files:**
- Modify: `static/css/input.css`

- [ ] **Step 1: Locate avatar dropdown rules**

Run: `grep -n '\.account-avatar-dropdown' static/css/input.css`

Confirm the existing rules live around lines 2066-2100.

- [ ] **Step 2: Update dropdown menu surface**

Find `.account-avatar-dropdown-menu { ... }` and replace with:

```css
  .account-avatar-dropdown-menu {
    position: absolute;
    right: 0;
    top: 100%;
    margin-top: 8px;
    min-width: 180px;
    padding: 6px;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: 4px;
    box-shadow: 0 12px 36px rgba(0, 0, 0, 0.5);
    z-index: 40;
    display: none;
    opacity: 0;
    transform: scale(0.98);
    transition:
      opacity var(--duration-modal) var(--easing-measured),
      transform var(--duration-modal) var(--easing-measured);
  }
  .account-avatar-dropdown-menu.open {
    display: block;
    opacity: 1;
    transform: scale(1);
  }
```

- [ ] **Step 3: Update dropdown item styling**

Find `.account-avatar-dropdown-menu a, .account-avatar-dropdown-menu button { ... }` and replace with:

```css
  .account-avatar-dropdown-menu a,
  .account-avatar-dropdown-menu button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    width: 100%;
    padding: 8px 10px;
    border: 0;
    border-radius: 3px;
    background: none;
    font-family: var(--font-sans);
    font-size: 10.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--color-text-muted);
    text-align: left;
    text-decoration: none;
    cursor: pointer;
    transition:
      background var(--duration-fast) var(--easing-default),
      color var(--duration-fast) var(--easing-default);
  }
  .account-avatar-dropdown-menu a:hover,
  .account-avatar-dropdown-menu button:hover {
    background: color-mix(in srgb, var(--color-accent) 10%, transparent);
    color: var(--color-accent);
  }
  .account-avatar-dropdown-menu .avatar-menu-divider {
    height: 1px;
    background: var(--color-border);
    margin: 4px 0;
    border: 0;
  }
  .account-avatar-dropdown-menu .avatar-menu-state {
    font-family: var(--font-serif);
    font-style: italic;
    font-weight: 400;
    font-size: 10px;
    letter-spacing: 0;
    text-transform: none;
    color: var(--color-text-muted);
  }
  .account-avatar-dropdown-menu .avatar-menu-logout {
    color: color-mix(in srgb, var(--color-text) 50%, transparent);
  }
```

- [ ] **Step 4: Update mobile panel links to match new typography**

Find `.navbar-mobile-links a, .navbar-mobile-links button { ... }` (around line 108) and replace with:

```css
  .navbar-mobile-links a,
  .navbar-mobile-links button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 10px 12px;
    background: none;
    border: 0;
    border-radius: 3px;
    font-family: var(--font-sans);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--color-text-muted);
    text-align: left;
    text-decoration: none;
    cursor: pointer;
    transition:
      background var(--duration-fast) var(--easing-default),
      color var(--duration-fast) var(--easing-default);
  }
  .navbar-mobile-links a:hover,
  .navbar-mobile-links button:hover {
    background: color-mix(in srgb, var(--color-accent) 10%, transparent);
    color: var(--color-accent);
  }
  .navbar-mobile-links .mobile-menu-state {
    font-family: var(--font-serif);
    font-style: italic;
    font-weight: 400;
    font-size: 10px;
    letter-spacing: 0;
    text-transform: none;
    color: var(--color-text-muted);
  }
```

- [ ] **Step 5: Rebuild CSS**

Run: `npm run build-css`
Expected: `output.css` regenerated.

- [ ] **Step 6: Commit**

```bash
git add static/css/input.css static/css/output.css
git commit -m "feat(navbar): update avatar dropdown + mobile panel styling"
```

---

## Task 6: Spotlight Modal Styles

**Files:**
- Modify: `static/css/input.css`

- [ ] **Step 1: Append Spotlight styles at the end of `@layer components`**

Find the closing `}` of the `@layer components { ... }` block (currently near the end of the file). Immediately before that closing brace, add:

```css
  /* ── Spotlight search modal ───────────────── */
  .search-spotlight-backdrop {
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    display: none;
    opacity: 0;
    transition: opacity var(--duration-modal) var(--easing-measured);
  }
  .search-spotlight-backdrop.open {
    display: block;
    opacity: 1;
  }

  .search-spotlight {
    position: fixed;
    top: 80px;
    left: 50%;
    transform: translateX(-50%) scale(0.98);
    z-index: 101;
    width: calc(100% - 32px);
    max-width: 560px;
    padding: 16px;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: 6px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.7);
    display: none;
    opacity: 0;
    transition:
      opacity var(--duration-modal) var(--easing-measured),
      transform var(--duration-modal) var(--easing-measured);
  }
  .search-spotlight.open {
    display: block;
    opacity: 1;
    transform: translateX(-50%) scale(1);
  }
  .search-spotlight-input {
    width: 100%;
    padding: 8px 10px;
    border: 0;
    border-bottom: 1px solid color-mix(in srgb, var(--color-text) 8%, transparent);
    background: transparent;
    font-family: var(--font-serif);
    font-style: italic;
    font-size: 18px;
    color: var(--color-text);
    outline: 0;
  }
  .search-spotlight-input::placeholder {
    color: var(--color-text-muted);
    font-style: italic;
  }
  .search-spotlight-results {
    margin: 10px 0 0;
    padding: 0;
    list-style: none;
    max-height: 400px;
    overflow-y: auto;
  }
  .search-spotlight-result {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: 3px;
    text-decoration: none;
    color: var(--color-text);
    cursor: pointer;
  }
  .search-spotlight-result:hover,
  .search-spotlight-result.is-active {
    background: color-mix(in srgb, var(--color-accent) 8%, transparent);
  }
  .search-spotlight-result-thumb {
    width: 40px;
    height: 60px;
    border-radius: 2px;
    background: linear-gradient(135deg, color-mix(in srgb, var(--color-accent) 30%, #0f0a08), #0f0a08);
    background-size: cover;
    background-position: center;
    flex-shrink: 0;
  }
  .search-spotlight-result-title {
    font-family: var(--font-sans);
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text);
  }
  .search-spotlight-result-meta {
    margin-left: auto;
    font-family: var(--font-serif);
    font-style: italic;
    font-size: 11px;
    color: var(--color-text-muted);
  }
  .search-spotlight-empty {
    padding: 16px 10px;
    font-family: var(--font-serif);
    font-style: italic;
    font-size: 13px;
    color: var(--color-text-muted);
    text-align: center;
  }

  @media (max-width: 640px) {
    .search-spotlight {
      top: 70px;
      padding: 12px;
    }
    .search-spotlight-input { font-size: 16px; }
  }
```

- [ ] **Step 2: Rebuild CSS**

Run: `npm run build-css`

- [ ] **Step 3: Commit**

```bash
git add static/css/input.css static/css/output.css
git commit -m "feat(search): add Spotlight modal styles"
```

---

## Task 7: Restructure `navbar_modern.html` Template

**Files:**
- Modify: `templates/navbar_modern.html`

- [ ] **Step 1: Replace the entire template**

Open `templates/navbar_modern.html`. Replace the **entire file** with:

```html
{% from "macros.html" import user_avatar with context %}

<!-- Navbar — scroll-aware editorial action bar -->
<header class="navbar" data-navbar>

  <!-- Brand lockup -->
  <a href="/" class="navbar-brand-wrap" aria-label="Nextreel home">
    <span class="navbar-brand">Nextreel</span>
    <span class="navbar-tagline">Cinema Discovery</span>
  </a>

  <!-- Desktop actions -->
  <nav class="navbar-actions hidden md:flex" aria-label="Primary">

    <!-- Search trigger -->
    <button type="button" class="navbar-icon-btn" id="searchSpotlightTrigger"
            aria-label="Open search" aria-haspopup="dialog" aria-controls="searchSpotlight">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="11" cy="11" r="7"/>
        <path d="m21 21-4.3-4.3"/>
      </svg>
    </button>

    {% if current_user_id %}
    <!-- Watched top-level link -->
    <a href="{{ url_for('main.watched_list_page') }}" class="navbar-link">Watched</a>

    <!-- Pick a Movie primary action -->
    <a href="/next_movie" class="navbar-pill"
       onclick="event.preventDefault(); document.getElementById('pickMovieFormNav').submit();">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3l14 9-14 9V3z"/></svg>
      <span>Pick a Movie</span>
    </a>
    <form id="pickMovieFormNav" method="POST" action="/next_movie" class="hidden">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    </form>

    <!-- Avatar dropdown -->
    <div class="account-avatar-dropdown">
      <button type="button" class="account-avatar-dropdown-trigger" id="avatarBtn"
              aria-haspopup="menu" aria-expanded="false" aria-controls="avatarMenu"
              aria-label="Account menu">
        {{ user_avatar(current_user, 'sm') }}
      </button>
      <div id="avatarMenu" class="account-avatar-dropdown-menu" role="menu">
        <a href="{{ url_for('main.account_view') }}?tab=profile" role="menuitem">Account</a>
        <div class="avatar-menu-divider" role="separator"></div>
        <button type="button" role="menuitem" id="themeToggleDesktop" data-theme-toggle>
          <span>Theme</span>
          <span class="avatar-menu-state" data-theme-state>Dark ●</span>
        </button>
        <div class="avatar-menu-divider" role="separator"></div>
        <form method="POST" action="/logout" style="display:contents;">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <button type="submit" role="menuitem" class="avatar-menu-logout">Log out</button>
        </form>
      </div>
    </div>

    {% else %}
    <!-- Logged-out: Pick stays primary; Log In as simple link -->
    <a href="/next_movie" class="navbar-pill"
       onclick="event.preventDefault(); document.getElementById('pickMovieFormNav').submit();">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3l14 9-14 9V3z"/></svg>
      <span>Pick a Movie</span>
    </a>
    <form id="pickMovieFormNav" method="POST" action="/next_movie" class="hidden">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    </form>
    <a href="/login" class="navbar-link">Log In</a>
    {% endif %}
  </nav>

  <!-- Mobile compact actions -->
  <div class="navbar-actions md:hidden" style="gap: 10px;">
    <button type="button" class="navbar-icon-btn" id="searchSpotlightTriggerMobile"
            aria-label="Open search" aria-haspopup="dialog" aria-controls="searchSpotlight"
            style="width: 30px; height: 30px;">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="11" cy="11" r="7"/>
        <path d="m21 21-4.3-4.3"/>
      </svg>
    </button>
    <a href="/next_movie" class="navbar-pill"
       style="padding: 7px 12px; font-size: 9.5px;"
       onclick="event.preventDefault(); document.getElementById('pickMovieFormMobile').submit();">
      <span>Pick</span>
    </a>
    <form id="pickMovieFormMobile" method="POST" action="/next_movie" class="hidden">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    </form>
    <button id="menuBtn" class="navbar-icon-btn" aria-controls="mobileMenu"
            aria-expanded="false" aria-label="Open menu"
            style="width: 30px; height: 30px;">
      <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style="stroke: none;">
        <path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z"/>
      </svg>
    </button>
  </div>
</header>

<!-- Mobile slide-down panel -->
<div id="mobileMenu" class="navbar-mobile-panel" role="dialog" aria-label="Navigation menu">
  <div class="navbar-mobile-close">
    <button id="menuClose" aria-label="Close menu">&times;</button>
  </div>
  <nav class="navbar-mobile-links">
    {% if current_user_id %}
    <a href="{{ url_for('main.watched_list_page') }}">Watched</a>
    <a href="{{ url_for('main.account_view') }}?tab=profile">Account</a>
    <button type="button" id="themeToggleMobile" data-theme-toggle>
      <span>Theme</span>
      <span class="mobile-menu-state" data-theme-state>Dark ●</span>
    </button>
    <form method="POST" action="/logout" style="display:contents;">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Log Out</button>
    </form>
    {% else %}
    <a href="/login">Log In</a>
    <button type="button" id="themeToggleMobile" data-theme-toggle>
      <span>Theme</span>
      <span class="mobile-menu-state" data-theme-state>Dark ●</span>
    </button>
    {% endif %}
  </nav>
</div>

<!-- Spotlight search modal -->
{% include '_search_spotlight.html' %}

<!-- Theme, dropdown, mobile, scroll, search scripts -->
<script>
  (function() {
    function setTheme(next) {
      try { localStorage.setItem('nr-theme', next); } catch (e) {}
      document.documentElement.setAttribute('data-theme', next);
      document.querySelectorAll('[data-theme-state]').forEach(function (el) {
        el.textContent = next === 'dark' ? 'Dark ●' : 'Light ○';
      });
    }

    // Mobile menu open/close
    var menuBtn = document.getElementById('menuBtn');
    var menuClose = document.getElementById('menuClose');
    var mobileMenu = document.getElementById('mobileMenu');
    if (menuBtn && mobileMenu) {
      menuBtn.addEventListener('click', function () {
        mobileMenu.classList.add('open');
        menuBtn.setAttribute('aria-expanded', 'true');
      });
    }
    if (menuClose && mobileMenu) {
      menuClose.addEventListener('click', function () {
        mobileMenu.classList.remove('open');
        if (menuBtn) menuBtn.setAttribute('aria-expanded', 'false');
      });
    }

    // Theme toggle (any element with data-theme-toggle)
    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var current = document.documentElement.getAttribute('data-theme') || 'dark';
        setTheme(current === 'dark' ? 'light' : 'dark');
      });
    });

    // Avatar dropdown
    var avatarBtn = document.getElementById('avatarBtn');
    var avatarMenu = document.getElementById('avatarMenu');
    if (avatarBtn && avatarMenu) {
      avatarBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        var isOpen = avatarMenu.classList.toggle('open');
        avatarBtn.setAttribute('aria-expanded', String(isOpen));
      });
      document.addEventListener('click', function () {
        avatarMenu.classList.remove('open');
        avatarBtn.setAttribute('aria-expanded', 'false');
      });
      avatarMenu.addEventListener('click', function (e) { e.stopPropagation(); });
    }

    // Sync theme state on load
    try {
      var pref = localStorage.getItem('nr-theme');
      if (pref === 'dark' || pref === 'light') setTheme(pref);
      else {
        var current = document.documentElement.getAttribute('data-theme') || 'dark';
        document.querySelectorAll('[data-theme-state]').forEach(function (el) {
          el.textContent = current === 'dark' ? 'Dark ●' : 'Light ○';
        });
      }
    } catch (e) {}
  })();
</script>
<script src="{{ url_for('static', filename='js/navbar-scroll.js') }}?v={{ config.get('CSS_VERSION', '1') }}" defer></script>
<script src="{{ url_for('static', filename='js/search-spotlight.js') }}?v={{ config.get('CSS_VERSION', '1') }}" defer></script>
```

- [ ] **Step 2: Smoke-test render**

Run: `python3 app.py` in one terminal.

In another: `curl -s http://127.0.0.1:5000/login | grep -E "navbar-brand|Cinema Discovery|searchSpotlightTrigger"`
Expected: All three strings appear (navbar-brand class present, tagline rendered, search trigger present).

Kill the dev server once verified.

- [ ] **Step 3: Commit**

```bash
git add templates/navbar_modern.html
git commit -m "feat(navbar): restructure template — brand lockup, Pick pill, search trigger, updated dropdown"
```

---

## Task 8: Spotlight Modal Template + Scroll Listener

**Files:**
- Create: `templates/_search_spotlight.html`
- Create: `static/js/navbar-scroll.js`

- [ ] **Step 1: Create `_search_spotlight.html`**

Create `templates/_search_spotlight.html`:

```html
<!-- Spotlight search modal — triggered by navbar search icon or `/` keybind -->
<div class="search-spotlight-backdrop" id="searchSpotlightBackdrop" aria-hidden="true"></div>
<div class="search-spotlight" id="searchSpotlight" role="dialog" aria-modal="true"
     aria-label="Search films" aria-hidden="true">
  <input type="search"
         class="search-spotlight-input"
         id="searchSpotlightInput"
         placeholder="Search films, actors…"
         autocomplete="off"
         spellcheck="false"
         aria-controls="searchSpotlightResults"
         aria-autocomplete="list">
  <ul class="search-spotlight-results" id="searchSpotlightResults" role="listbox" aria-label="Search results">
    <li class="search-spotlight-empty" id="searchSpotlightEmpty">Start typing to search…</li>
  </ul>
</div>
```

- [ ] **Step 2: Create `navbar-scroll.js`**

Create `static/js/navbar-scroll.js`:

```javascript
/**
 * Navbar scroll-aware surface toggle.
 *
 * Adds `.navbar--solid` to `[data-navbar]` when scrollY > THRESHOLD.
 * rAF-throttled so the listener is cheap even on long pages.
 */
(function () {
  var THRESHOLD = 40;
  var navbar = document.querySelector('[data-navbar]');
  if (!navbar) return;

  var ticking = false;
  var isSolid = false;

  function update() {
    var nextSolid = window.scrollY > THRESHOLD;
    if (nextSolid !== isSolid) {
      isSolid = nextSolid;
      navbar.classList.toggle('navbar--solid', isSolid);
    }
    ticking = false;
  }

  function onScroll() {
    if (!ticking) {
      window.requestAnimationFrame(update);
      ticking = true;
    }
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  // Handle browser-restored scroll position on back/forward nav.
  update();
})();
```

- [ ] **Step 3: Verify files exist and are non-empty**

Run: `wc -l templates/_search_spotlight.html static/js/navbar-scroll.js`
Expected: `_search_spotlight.html` ~15 lines, `navbar-scroll.js` ~30 lines.

- [ ] **Step 4: Commit**

```bash
git add templates/_search_spotlight.html static/js/navbar-scroll.js
git commit -m "feat(navbar): add Spotlight modal template + scroll-aware listener"
```

---

## Task 9: Spotlight Modal JS Behavior

**Files:**
- Create: `static/js/search-spotlight.js`

- [ ] **Step 1: Create the module**

Create `static/js/search-spotlight.js`:

```javascript
/**
 * Spotlight search modal controller.
 *
 * Opens on:
 *   - Click of `#searchSpotlightTrigger` / `#searchSpotlightTriggerMobile`
 *   - `/` keypress anywhere on the page (unless a text input is focused)
 *
 * Closes on:
 *   - Escape key
 *   - Click on backdrop
 *   - Result selection
 */
(function () {
  var DEBOUNCE_MS = 150;
  var MIN_QUERY_LENGTH = 2;

  var backdrop = document.getElementById('searchSpotlightBackdrop');
  var modal = document.getElementById('searchSpotlight');
  var input = document.getElementById('searchSpotlightInput');
  var resultsEl = document.getElementById('searchSpotlightResults');
  var desktopTrigger = document.getElementById('searchSpotlightTrigger');
  var mobileTrigger = document.getElementById('searchSpotlightTriggerMobile');

  if (!backdrop || !modal || !input || !resultsEl) return;

  var debounceTimer = null;
  var currentResults = [];
  var activeIndex = -1;
  var lastFocusedElement = null;

  function openModal() {
    lastFocusedElement = document.activeElement;
    backdrop.classList.add('open');
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    backdrop.setAttribute('aria-hidden', 'false');
    [desktopTrigger, mobileTrigger].forEach(function (t) {
      if (t) t.setAttribute('aria-expanded', 'true');
    });
    // Delay focus until the transition frame so CSS can settle.
    window.requestAnimationFrame(function () { input.focus(); input.select(); });
  }

  function closeModal() {
    backdrop.classList.remove('open');
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    backdrop.setAttribute('aria-hidden', 'true');
    [desktopTrigger, mobileTrigger].forEach(function (t) {
      if (t) t.setAttribute('aria-expanded', 'false');
    });
    input.value = '';
    renderEmpty('Start typing to search…');
    if (lastFocusedElement && lastFocusedElement.focus) {
      lastFocusedElement.focus();
    }
  }

  function renderEmpty(message) {
    resultsEl.innerHTML = '';
    var li = document.createElement('li');
    li.className = 'search-spotlight-empty';
    li.textContent = message;
    resultsEl.appendChild(li);
    currentResults = [];
    activeIndex = -1;
  }

  function renderResults(results) {
    resultsEl.innerHTML = '';
    if (!results.length) {
      renderEmpty('No films found.');
      return;
    }
    currentResults = results;
    results.forEach(function (r, idx) {
      var li = document.createElement('li');
      var a = document.createElement('a');
      a.href = '/movie/' + encodeURIComponent(r.tconst);
      a.className = 'search-spotlight-result';
      a.setAttribute('role', 'option');
      a.dataset.index = String(idx);

      var thumb = document.createElement('span');
      thumb.className = 'search-spotlight-result-thumb';
      // Posters require TMDb enrichment that `movie_candidates` doesn't carry.
      // The gradient placeholder provides consistent visual rhythm.

      var title = document.createElement('span');
      title.className = 'search-spotlight-result-title';
      title.textContent = r.title || 'Untitled';

      var meta = document.createElement('span');
      meta.className = 'search-spotlight-result-meta';
      var metaParts = [];
      if (r.year) metaParts.push(r.year);
      if (typeof r.rating === 'number' && r.rating > 0) {
        metaParts.push('★ ' + r.rating.toFixed(1));
      }
      meta.textContent = metaParts.join(' · ');

      a.appendChild(thumb);
      a.appendChild(title);
      if (metaParts.length) a.appendChild(meta);
      li.appendChild(a);
      resultsEl.appendChild(li);
    });
    activeIndex = -1;
  }

  function setActiveIndex(idx) {
    var rows = resultsEl.querySelectorAll('.search-spotlight-result');
    rows.forEach(function (r) { r.classList.remove('is-active'); r.setAttribute('aria-selected', 'false'); });
    if (idx < 0 || idx >= rows.length) {
      activeIndex = -1;
      return;
    }
    rows[idx].classList.add('is-active');
    rows[idx].setAttribute('aria-selected', 'true');
    activeIndex = idx;
  }

  function performSearch(q) {
    var trimmed = (q || '').trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      renderEmpty('Start typing to search…');
      return;
    }
    fetch('/api/search?q=' + encodeURIComponent(trimmed), { credentials: 'same-origin' })
      .then(function (res) { return res.ok ? res.json() : { results: [] }; })
      .then(function (data) { renderResults((data && data.results) || []); })
      .catch(function () { renderEmpty("Couldn't reach the catalog. Try again."); });
  }

  input.addEventListener('input', function () {
    if (debounceTimer) clearTimeout(debounceTimer);
    var q = input.value;
    debounceTimer = setTimeout(function () { performSearch(q); }, DEBOUNCE_MS);
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { e.preventDefault(); closeModal(); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(Math.min(activeIndex + 1, currentResults.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(Math.max(activeIndex - 1, 0));
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      var rows = resultsEl.querySelectorAll('.search-spotlight-result');
      if (rows[activeIndex]) rows[activeIndex].click();
    }
  });

  backdrop.addEventListener('click', closeModal);
  if (desktopTrigger) desktopTrigger.addEventListener('click', openModal);
  if (mobileTrigger) mobileTrigger.addEventListener('click', openModal);

  // Global `/` keybind — ignored when a text input is focused.
  document.addEventListener('keydown', function (e) {
    if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
    var target = e.target;
    var isEditable = target && (
      target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.isContentEditable
    );
    if (isEditable) return;
    e.preventDefault();
    openModal();
  });
})();
```

- [ ] **Step 2: Verify file exists and loads in the app**

Run: `python3 app.py` in one terminal.
In another: `curl -s http://127.0.0.1:5000/login | grep search-spotlight.js`
Expected: The script tag is present in the rendered HTML.

Kill the dev server once verified.

- [ ] **Step 3: Commit**

```bash
git add static/js/search-spotlight.js
git commit -m "feat(search): implement Spotlight modal controller (open/close, debounced fetch, keyboard nav)"
```

---

## Task 10: Manual Validation Sweep

**Files:**
- None modified; verification only.

- [ ] **Step 1: Start dev server**

Run: `python3 app.py`

- [ ] **Step 2: Visual check — desktop transparent state**

Open `http://127.0.0.1:5000/movie/<any-tconst-from-your-db>` in a browser.

Verify:
- Brand "Nextreel" renders in Merriweather 22px
- Tagline "CINEMA DISCOVERY" visible below brand in tracked caps
- Search icon (⌕) present to the right
- "Watched" link visible
- Pick a Movie pill in terracotta with the triangle icon
- Avatar circle rightmost
- Bar background is transparent — hero image shows through

- [ ] **Step 3: Visual check — scroll transition**

On the movie page, scroll down. At ~40px, the bar background should fade to solid (near-black in dark mode). Scroll back up — bar fades back to transparent. Transition is 250ms, smooth.

- [ ] **Step 4: Visual check — avatar dropdown**

Click the avatar. Dropdown appears with:
- Account
- Theme · Dark ● (italic serif state)
- Log Out (half-opacity)

Click Theme. Light mode activates; state updates to "Light ○" in both dropdown AND (via `data-theme-state`) the mobile panel.

Click outside dropdown → closes.

- [ ] **Step 5: Visual check — Spotlight modal**

Click the search icon. Backdrop dims the page; centered modal appears with italic-serif placeholder "Search films, actors…".

Type `drama` (or any 2+ char query). Results appear debounced. Press `↓` to navigate, `Enter` to open the selected movie.

Press `/` anywhere on the page (outside an input) — modal opens.
Press `Esc` — modal closes; focus returns to the search icon.

- [ ] **Step 6: Visual check — mobile (375px)**

Open Chrome DevTools → device toolbar → iPhone SE (375×667).

Verify:
- Brand + tagline visible
- Search icon + "Pick" pill (compressed) + hamburger visible
- Tap hamburger → slide-down panel opens with Watched / Account / Theme (italic state) / Log Out
- Tap search icon → Spotlight opens full-width with 16px margins
- Tap Pick → submits form, navigates

- [ ] **Step 7: Visual check — pages without a hero**

Navigate to `/watched`, `/account`, `/login`, `/register`.
- On /login and /register, since they scroll (or their pages are tall enough), verify the bar goes solid immediately.
- On /watched, the bar should be transparent at the very top (over the page header) and solidify on scroll.

- [ ] **Step 8: Logged-out layout**

Open `/login` (or any page) while unauthenticated.
- No avatar, no Watched link
- Pick a Movie pill still visible
- "Log In" as a nav-link (not pill)

- [ ] **Step 9: Reduced motion**

System Settings → Accessibility → enable "Reduce motion."
- Scroll transition happens instantly (no 250ms fade)
- Modal appears instantly on open

(Or temporarily add `prefers-reduced-motion: reduce` to your browser via DevTools emulation: Cmd-Shift-P → "Emulate CSS media feature prefers-reduced-motion" → reduce.)

- [ ] **Step 10: Keyboard accessibility**

Tab through the bar from the skip-to-content link. Verify focus rings:
- Brand (2px accent outline)
- Search icon button
- Watched link
- Pick pill
- Avatar button

Press Enter on avatar → dropdown opens. Tab through items → each shows focus ring. Esc → dropdown closes, focus returns to avatar button.

- [ ] **Step 11: Run lint + type checks + tests**

```bash
black . --line-length 100
flake8 . --exclude=venv,node_modules
mypy . --ignore-missing-imports
python3 -m pytest tests/ -v
```

Expected: No new errors introduced. All tests (including the 5+9 new test cases) pass.

- [ ] **Step 12: Commit any fixes**

If Step 11 surfaces lint / type / test issues, fix them and commit:

```bash
git add <fixed-files>
git commit -m "fix(navbar): address lint/type/test issues from validation sweep"
```

---

## Self-Review Checklist

After completing all tasks, verify:

- [ ] **Spec coverage** — each row in the "Design Decisions Summary" table of the spec has at least one corresponding task:
  - Brand + tagline → Task 4 (CSS) + Task 7 (template)
  - ⌕ icon + Spotlight modal → Tasks 2, 3, 6, 8, 9
  - Watched top-level → Task 7
  - Pick pill → Tasks 4, 7
  - Avatar dropdown → Tasks 5, 7
  - Scroll-aware surface → Tasks 4, 8
  - Mobile compact → Tasks 5, 7
  - Motion system → Tasks 1, 4
  - Logged-out state → Task 7
  - Accessibility → Task 10 (validation steps 9-10)
- [ ] **No placeholders** — every code block has real content; no "TBD" / "similar to earlier."
- [ ] **Type consistency** — the JSON shape returned by `/api/search` (Task 3) matches what `search-spotlight.js` consumes (Task 9): `{results: [{tconst, title, year, rating}]}`. Director and poster are not returned (movie_candidates schema does not carry them; adding them would require per-row lookups into `movie_projection.payload_json`).
- [ ] **No changes to out-of-scope files** — `home.html`, filter drawer files, floating arrow buttons: all untouched.

---

## Out of Scope (explicit non-goals)

- No changes to `home.html` — it keeps its bespoke absolute-positioned top treatment.
- No changes to the filter drawer (`templates/_filter_form.html`, `static/js/filter-drawer.js`, drawer tab button).
- No changes to floating `Previous` / `Next` `.arrow-btn` controls on the movie hero.
- No theme picker — only light↔dark toggle (preserved from current behavior).
- No notifications, recent search history, streaming-availability chips, or filter chips.
- No changes to user registration, OAuth flow, or session cookies.
