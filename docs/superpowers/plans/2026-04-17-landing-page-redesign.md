# Landing Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **User preference — no autocommit:** This user prefers to run `git commit` themselves. Every task ends with a "Prepare commit" step that shows the diff + suggested commit command — do NOT run `git commit` as part of the task. Leave the staged-and-ready state for the user to finalize.

**Goal:** Replace the current splash landing page with a Criterion-style film spotlight that picks a random enriched film from `movie_projection` on every page load and displays its TMDb backdrop, title, director, and runtime as the hero.

**Architecture:** One new backend helper (`fetch_random_landing_film`) queries `movie_projection` for a random READY-state row whose payload has a TMDb backdrop URL. The `home()` route threads that dict into the template context, falling back to a hardcoded 3-film pool if the DB is empty. Frontend is a full-viewport hero built with new `.landing-*` CSS classes using Bebas Neue for the title. No new routes, no new models, no scroll.

**Tech Stack:** Quart, aiomysql (via existing pool), Jinja2, Tailwind CSS v3, pytest-asyncio, Google Fonts (Bebas Neue added)

**Spec:** `docs/superpowers/specs/2026-04-17-landing-page-redesign-design.md`

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `movies/landing_film_service.py` | `fetch_random_landing_film(pool)` helper + `_clean()` sentinel scrubber |
| `tests/movies/test_landing_film_service.py` | Tests for the helper (empty pool, dict shape, sentinel scrubbing, payload-as-string vs dict handling) |

### Modified files
| File | Change |
|------|--------|
| `static/css/tokens.css` | Add `--font-display: 'Bebas Neue', ...` token |
| `static/css/input.css` | Add `.landing-*` component block (hero frame, title, kicker, meta, CTAs, side label, credit, motion, responsive) |
| `static/css/output.css` | Regenerate via `npm run build-css` |
| `templates/home.html` | Rewrite body markup; load Bebas Neue via Google Fonts; keep navbar include and film-grain overlay |
| `nextreel/web/routes/movies.py` | Update `home()` to fetch landing film + use fallback pool; add `_LANDING_FALLBACK_POOL` module constant |
| `tests/web/test_routes_home.py` | NEW or extend existing — assert `home()` renders with `landing_film` context; covers both DB-populated and fallback paths |

### Design decisions locked in here
- **New service module** rather than bloating `projection_read_service.py` (landing concern is distinct from the stateful projection-render policy).
- **Module-level fallback pool** rather than a JSON file — pool is static, small, and safe to version-control.
- **Display-ready strings from payload** — `directors`, `runtime`, `year` are stored pre-formatted by `movie_payload.py`, so no re-formatting at render time. The template just renders them with ` · ` separators.

---

## Task 1: Add `--font-display` token

**Files:**
- Modify: `static/css/tokens.css`

- [ ] **Step 1: Add the token to the `:root` block**

Open `static/css/tokens.css`. Find the block starting at line 2 (`:root {`). Locate the other font tokens (search for `--font-sans` — should be around line 19). Immediately after the `--font-serif: ...;` line, add:

```css
  --font-display: 'Bebas Neue', 'Arial Narrow', 'Helvetica Neue Condensed', sans-serif;
```

- [ ] **Step 2: Verify**

Run: `grep -n "font-display" static/css/tokens.css`
Expected: 1 match, in the `:root` block near the other font tokens.

- [ ] **Step 3: Rebuild CSS (tokens.css is referenced separately, but rebuild to keep the file fresh)**

Run: `npm run build-css`
Expected: No errors, `Done in Nms.` message.

- [ ] **Step 4: Prepare commit (DO NOT commit)**

Show the user the diff. Suggested command:
```bash
git add static/css/tokens.css
git commit -m "feat(landing): add --font-display token (Bebas Neue stack)"
```

---

## Task 2: Create `fetch_random_landing_film` helper (TDD)

**Files:**
- Create: `movies/landing_film_service.py`
- Create: `tests/movies/test_landing_film_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/movies/test_landing_film_service.py`:

```python
"""Tests for movies.landing_film_service.fetch_random_landing_film."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from movies.landing_film_service import fetch_random_landing_film, _clean


def test_clean_returns_value_for_real_strings():
    assert _clean("Wong Kar-wai") == "Wong Kar-wai"
    assert _clean("102 min") == "102 min"
    assert _clean("1994") == "1994"


def test_clean_returns_none_for_sentinels():
    assert _clean(None) is None
    assert _clean("") is None
    assert _clean("Unknown") is None
    assert _clean("N/A") is None
    assert _clean("0 min") is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_pool_empty():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[])
    result = await fetch_random_landing_film(pool)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_flat_dict_from_row_with_dict_payload():
    """payload_json comes back already-parsed as a dict from aiomysql (recent drivers)."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[{
        "tconst": "tt0109424",
        "payload_json": {
            "title": "Chungking Express",
            "year": "1994",
            "directors": "Wong Kar-wai",
            "runtime": "102 min",
            "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
        },
    }])
    result = await fetch_random_landing_film(pool)
    assert result == {
        "tconst": "tt0109424",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }


@pytest.mark.asyncio
async def test_fetch_returns_flat_dict_from_row_with_string_payload():
    """payload_json comes back as a JSON-encoded string from some driver versions."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[{
        "tconst": "tt0118694",
        "payload_json": json.dumps({
            "title": "In the Mood for Love",
            "year": "2000",
            "directors": "Wong Kar-wai",
            "runtime": "98 min",
            "backdrop_url": "https://image.tmdb.org/t/p/original/bar.jpg",
        }),
    }])
    result = await fetch_random_landing_film(pool)
    assert result["title"] == "In the Mood for Love"
    assert result["director"] == "Wong Kar-wai"


@pytest.mark.asyncio
async def test_fetch_scrubs_sentinel_values_for_missing_metadata():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[{
        "tconst": "tt000001",
        "payload_json": {
            "title": "Partial Record",
            "year": "N/A",
            "directors": "Unknown",
            "runtime": "0 min",
            "backdrop_url": "https://image.tmdb.org/t/p/original/x.jpg",
        },
    }])
    result = await fetch_random_landing_film(pool)
    assert result["title"] == "Partial Record"
    assert result["year"] is None
    assert result["director"] is None
    assert result["runtime"] is None
    assert result["backdrop_url"] == "https://image.tmdb.org/t/p/original/x.jpg"


@pytest.mark.asyncio
async def test_fetch_sql_filters_to_ready_state_with_tmdb_backdrop():
    """The SQL must restrict to READY + TMDb-sourced backdrops."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[])
    await fetch_random_landing_film(pool)
    # First positional arg to pool.execute is the SQL
    sql = pool.execute.call_args.args[0]
    assert "movie_projection" in sql
    assert "projection_state = 'ready'" in sql
    assert "image.tmdb.org" in sql
    assert "ORDER BY RAND()" in sql
    assert "LIMIT 1" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_landing_film_service.py -v`
Expected: All tests fail with `ModuleNotFoundError: No module named 'movies.landing_film_service'`.

- [ ] **Step 3: Create the module**

Create `movies/landing_film_service.py`:

```python
"""Random-film picker for the Criterion-style landing page.

Queries movie_projection for one READY row whose payload carries a real
TMDb backdrop URL, and returns a flat dict ready for template rendering.
Separate from projection_read_service because its concern (landing hero
selection) has no relationship to the stateful render-policy logic there.
"""

from __future__ import annotations

import json
from typing import Any

from logging_config import get_logger

logger = get_logger(__name__)

_LANDING_SENTINELS = ("Unknown", "N/A", "", "0 min")

_LANDING_SQL = (
    "SELECT tconst, payload_json "
    "FROM movie_projection "
    "WHERE projection_state = 'ready' "
    "  AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.backdrop_url')) LIKE 'https://image.tmdb.org/%' "
    "ORDER BY RAND() "
    "LIMIT 1"
)


def _clean(value: Any) -> Any:
    """Return None for the payload_factory's 'missing-field' sentinels."""
    if value is None or value in _LANDING_SENTINELS:
        return None
    return value


async def fetch_random_landing_film(pool) -> dict[str, Any] | None:
    """Pick one enriched film with a TMDb-sourced backdrop, at random.

    Returns a flat dict ready for template use, or None if no qualifying
    rows exist. Callers should apply a hardcoded fallback pool when None.
    """
    try:
        rows = await pool.execute(_LANDING_SQL, (), fetch="all")
    except Exception as exc:  # noqa: BLE001 — defense-in-depth, degrade silently
        logger.warning("Landing-film query failed: %s", exc)
        return None
    if not rows:
        return None

    row = rows[0]
    payload_raw = row["payload_json"]
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw

    return {
        "tconst": row["tconst"],
        "title": payload.get("title"),
        "year": _clean(payload.get("year")),
        "director": _clean(payload.get("directors")),
        "runtime": _clean(payload.get("runtime")),
        "backdrop_url": payload.get("backdrop_url"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_landing_film_service.py -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Verify no regression in other projection/movie tests**

Run: `python3 -m pytest tests/movies/ tests/infra/test_runtime_schema.py -q --no-header`
Expected: No failures introduced.

- [ ] **Step 6: Prepare commit (DO NOT commit)**

Show the user the diff. Suggested command:
```bash
git add movies/landing_film_service.py tests/movies/test_landing_film_service.py
git commit -m "feat(landing): add fetch_random_landing_film picker + tests"
```

---

## Task 3: Wire the picker into the `home()` route

**Files:**
- Modify: `nextreel/web/routes/movies.py`
- Create: `tests/web/test_routes_home.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_routes_home.py`:

```python
"""Tests for the / (home/landing) route integration."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import TEST_ENV


@pytest.fixture
def test_client():
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool = MagicMock()

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        yield app.test_client()


@pytest.mark.asyncio
async def test_home_route_renders_with_db_sourced_film(test_client):
    fake_film = {
        "tconst": "tt0109424",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=fake_film),
    ):
        response = await test_client.get("/")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        # Title must render
        assert "Chungking Express" in body
        # Backdrop URL must appear (in the inline style or element)
        assert "foo.jpg" in body
        # Metadata tokens must appear
        assert "1994" in body
        assert "Wong Kar-wai" in body
        assert "102 min" in body


@pytest.mark.asyncio
async def test_home_route_falls_back_when_db_empty(test_client):
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=None),
    ):
        response = await test_client.get("/")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        # One of the three fallback films must render
        fallback_titles = ("Chungking Express", "2001: A Space Odyssey", "In the Mood for Love")
        assert any(t in body for t in fallback_titles)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_routes_home.py -v`
Expected: Tests fail — import error on `fetch_random_landing_film` in `nextreel.web.routes.movies`, or assertion errors (the current template doesn't render these values).

- [ ] **Step 3: Update `nextreel/web/routes/movies.py`**

Open `nextreel/web/routes/movies.py`. Add imports at the top (after the existing `from quart ...` line):

```python
import random

from movies.landing_film_service import fetch_random_landing_film
```

Then, immediately above the existing `@bp.route("/")` line, add the fallback pool constant:

```python
_LANDING_FALLBACK_POOL = (
    {
        "tconst": "tt0109424",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/2jSCMkdS63uyMyXmc3dsDCAyiFb.jpg",
    },
    {
        "tconst": "tt0062622",
        "title": "2001: A Space Odyssey",
        "year": "1968",
        "director": "Stanley Kubrick",
        "runtime": "149 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/dMrAwwB7PMC4SjgsTbgmEJblaYd.jpg",
    },
    {
        "tconst": "tt0118694",
        "title": "In the Mood for Love",
        "year": "2000",
        "director": "Wong Kar-wai",
        "runtime": "98 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/iYBBeBMLyLR1R1eYMMvfAJLeiIr.jpg",
    },
)
```

Replace the existing `async def home():` body with:

```python
@bp.route("/")
async def home():
    state = _current_state()
    services = _services()
    data = await services.movie_manager.home(state, legacy_session=_legacy_session())

    landing_film = await fetch_random_landing_film(services.movie_manager.db_pool)
    if landing_film is None:
        landing_film = random.choice(_LANDING_FALLBACK_POOL)

    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
        landing_film=landing_film,
    )
```

- [ ] **Step 4: Run tests to verify the route passes** (template test assertion will still fail because home.html is unchanged)

Run: `python3 -m pytest tests/web/test_routes_home.py -v`
Expected: Both tests fail on the `"Chungking Express" in body` assertion — because `templates/home.html` doesn't render the `landing_film` context yet. **This is expected and desired** — Tasks 4 and 5 complete the visual side.

For now, verify the route CALLS `fetch_random_landing_film` correctly:

Run: `python3 -c "from nextreel.web.routes.movies import home, _LANDING_FALLBACK_POOL; print('home fn:', home); print('pool size:', len(_LANDING_FALLBACK_POOL))"`
Expected: `home fn: <function home at 0x...>` and `pool size: 3`

- [ ] **Step 5: Prepare commit (DO NOT commit)**

Show the user the diff. Suggested command:
```bash
git add nextreel/web/routes/movies.py tests/web/test_routes_home.py
git commit -m "feat(landing): wire fetch_random_landing_film into home() route with fallback pool"
```

Note for user: `tests/web/test_routes_home.py` will stay red until Task 5 rewrites the template. This is intentional — it's the failing-test fixture that drives Task 5.

---

## Task 4: Add `.landing-*` CSS

**Files:**
- Modify: `static/css/input.css`
- Regenerate: `static/css/output.css`

- [ ] **Step 1: Locate the end of `@layer components`**

Run: `grep -n "^}" static/css/input.css | tail -3`
Expected: Last `^}` before the `@layer utilities` block is the close of `@layer components`. Note that line number.

- [ ] **Step 2: Insert the `.landing-*` rules just before the close of `@layer components`**

Open `static/css/input.css`. Find the closing `}` of the `@layer components { ... }` block (should be near the end of the file, immediately before `@layer utilities`). Insert the following **above** that closing brace:

```css
  /* ── Landing page (Criterion-style film spotlight) ───────── */
  .landing-page {
    position: relative;
    height: 100vh;
    overflow: hidden;
    background: #0a0807;
    font-family: var(--font-sans);
    color: #fff;
  }
  .landing-bg {
    position: absolute; inset: 0; z-index: 0;
    background-size: cover;
    background-position: center;
    animation: landing-kenburns 40s ease-in-out infinite alternate;
  }
  @keyframes landing-kenburns {
    0%   { transform: scale(1.05) translate(0, 0); }
    100% { transform: scale(1.15) translate(-2%, -1%); }
  }
  .landing-gradient {
    position: absolute; inset: 0; z-index: 1;
    background: linear-gradient(
      180deg,
      rgba(0,0,0,0.4) 0%,
      rgba(0,0,0,0.08) 20%,
      rgba(0,0,0,0) 50%,
      rgba(0,0,0,0.5) 100%
    );
    pointer-events: none;
  }
  /* Reuse existing .home-grain overlay at z-index:2 — no new rule needed */

  .landing-side-label {
    position: absolute;
    left: 20px; top: 50%;
    transform: translateY(-50%) rotate(-90deg);
    transform-origin: left center;
    z-index: 3;
    font-family: var(--font-sans);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.55);
    white-space: nowrap;
    pointer-events: none;
  }

  .landing-content {
    position: absolute; inset: 0; z-index: 4;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 80px 60px 100px;
  }
  .landing-content > * {
    animation: landing-fadeup 600ms ease-out both;
  }
  .landing-content .landing-kicker { animation-delay: 150ms; }
  .landing-content .landing-title { animation-delay: 280ms; }
  .landing-content .landing-meta { animation-delay: 400ms; }
  .landing-content .landing-actions { animation-delay: 520ms; }
  @keyframes landing-fadeup {
    from { opacity: 0; transform: translateY(10px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .landing-kicker {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.28em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.72);
    margin-bottom: 24px;
  }
  .landing-kicker-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: #fff;
  }

  .landing-title {
    font-family: var(--font-display);
    font-size: clamp(64px, 12vw, 148px);
    font-weight: 400;
    line-height: 0.92;
    letter-spacing: 0.01em;
    color: #fff;
    text-shadow: 0 2px 30px rgba(0,0,0,0.5);
    margin: 0 0 12px;
    word-break: normal;
    overflow-wrap: break-word;
  }

  .landing-meta {
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.24em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.82);
    margin-bottom: 36px;
  }

  .landing-actions {
    display: flex;
    gap: 12px;
    align-items: center;
    justify-content: center;
  }
  .landing-cta-primary,
  .landing-cta-ghost {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 16px 32px;
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    text-decoration: none;
    border-radius: 0;
    cursor: pointer;
    border: 0;
  }
  .landing-cta-primary {
    background: #fff;
    color: #0a0807;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3);
    transition: background var(--duration-normal) var(--easing-default);
  }
  .landing-cta-primary:hover { background: #e8e6e3; }
  .landing-cta-primary:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 3px;
  }
  .landing-cta-ghost {
    background: transparent;
    color: #fff;
    border: 1px solid rgba(255,255,255,0.45);
    transition:
      background var(--duration-normal) var(--easing-default),
      border-color var(--duration-normal) var(--easing-default);
  }
  .landing-cta-ghost:hover {
    background: rgba(255,255,255,0.08);
    border-color: rgba(255,255,255,0.7);
  }
  .landing-cta-ghost:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 3px;
  }

  .landing-credit {
    position: absolute;
    bottom: 20px; right: 28px;
    z-index: 4;
    font-family: var(--font-serif);
    font-style: italic;
    font-size: 9px;
    color: rgba(255,255,255,0.45);
    pointer-events: none;
  }

  @media (max-width: 768px) {
    .landing-side-label { display: none; }
    .landing-content { padding: 60px 20px 80px; }
    .landing-title { font-size: clamp(48px, 16vw, 96px); }
    .landing-meta { font-size: 10px; letter-spacing: 0.2em; }
    .landing-actions {
      flex-direction: column;
      width: 100%;
      max-width: 320px;
      margin: 0 auto;
    }
    .landing-cta-primary,
    .landing-cta-ghost {
      width: 100%;
      justify-content: center;
    }
    .landing-credit {
      bottom: 14px; right: 14px;
      font-size: 8px;
    }
  }
```

- [ ] **Step 3: Rebuild Tailwind CSS**

Run: `npm run build-css`
Expected: `Done in Nms.` — no errors.

- [ ] **Step 4: Verify source CSS has the new classes**

Run: `grep -c "landing-title\|landing-kicker\|landing-bg\|landing-cta-primary\|landing-side-label\|landing-credit" static/css/input.css`
Expected: Positive count (10+ matches).

Note: `output.css` will not emit these classes until `home.html` references them (Tailwind purges unused classes). Task 5 adds the template references; Step 5 of Task 5 rebuilds.

- [ ] **Step 5: Prepare commit (DO NOT commit)**

Show the user the diff. Suggested command:
```bash
git add static/css/input.css static/css/output.css
git commit -m "feat(landing): add .landing-* styles (Criterion-style hero, Ken Burns motion, responsive)"
```

---

## Task 5: Rewrite `templates/home.html`

**Files:**
- Modify: `templates/home.html`

- [ ] **Step 1: Replace the entire template**

Open `templates/home.html`. Replace the ENTIRE file with:

```html
{% from "macros.html" import pick_movie_button with context %}
<!DOCTYPE html>
<html lang="en" {% if server_theme %}data-theme-server="{{ server_theme }}"{% endif %}>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nextreel – Cinema Discovery</title>
  <meta name="description" content="One random film at a time, pulled from a catalog of tens of thousands.">
  <link rel="preconnect" href="https://image.tmdb.org" crossorigin>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Merriweather:ital,wght@0,300;0,400;0,700;1,400&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/output.css') }}?v={{ config.get('CSS_VERSION', '1') }}">
  {% if landing_film and landing_film.backdrop_url %}
  <link rel="preload" as="image" href="{{ landing_film.backdrop_url }}" fetchpriority="high">
  {% endif %}
  <script>
    (() => {
      try {
        const pref = localStorage.getItem('nr-theme');
        if (pref === 'light' || pref === 'dark') {
          document.documentElement.setAttribute('data-theme', pref);
          return;
        }
        const server = document.documentElement.getAttribute('data-theme-server');
        if (server === 'light' || server === 'dark') {
          document.documentElement.setAttribute('data-theme', server);
        }
      } catch (e) {}
    })();
  </script>
  <style>
    body { font-family: 'DM Sans', system-ui, sans-serif; margin: 0; padding: 0; }
    html, body { height: 100%; overflow: hidden; }

    /* Film grain overlay — retained from previous design for analog texture */
    .home-grain {
      position: fixed; inset: 0; z-index: 2;
      pointer-events: none; opacity: 0.04;
      background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
      background-size: 256px 256px;
    }
  </style>
</head>
<body>

  <a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm">Skip to content</a>

  {% include 'navbar_modern.html' %}

  <main id="main" class="landing-page">
    <div class="landing-bg" style="background-image: url('{{ landing_film.backdrop_url }}');"></div>
    <div class="landing-gradient"></div>
    <div class="home-grain"></div>

    <div class="landing-side-label" aria-hidden="true">Random · No Sign-up</div>

    <div class="landing-content">
      <div class="landing-kicker"><span class="landing-kicker-dot"></span>Your random film</div>
      <h1 class="landing-title">{{ landing_film.title }}</h1>

      {% set meta_parts = [] %}
      {% if landing_film.year %}{% set _ = meta_parts.append(landing_film.year) %}{% endif %}
      {% if landing_film.director %}{% set _ = meta_parts.append(landing_film.director) %}{% endif %}
      {% if landing_film.runtime %}{% set _ = meta_parts.append(landing_film.runtime) %}{% endif %}
      {% if meta_parts %}
      <div class="landing-meta">{{ meta_parts | join(' · ') }}</div>
      {% endif %}

      <div class="landing-actions">
        <form method="POST" action="/next_movie" style="display:inline;">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <button type="submit" class="landing-cta-primary">Pick Another →</button>
        </form>
        <a class="landing-cta-ghost" href="{{ url_for('main.movie_detail', tconst=landing_film.tconst) }}">See this film ↗</a>
      </div>
    </div>

    {% if landing_film.title and landing_film.year %}
    <div class="landing-credit">Film still: {{ landing_film.title }} ({{ landing_film.year }})</div>
    {% endif %}
  </main>
</body>
</html>
```

- [ ] **Step 2: Rebuild Tailwind CSS (now that template references new classes)**

Run: `npm run build-css`
Expected: `Done in Nms.` — no errors.

- [ ] **Step 3: Verify new classes now emit in `output.css`**

Run: `for cls in landing-title landing-kicker landing-cta-primary landing-cta-ghost landing-side-label landing-credit; do count=$(grep -o "\.${cls}[^a-z-]" static/css/output.css 2>/dev/null | wc -l | tr -d ' '); echo "$cls: $count"; done`
Expected: Each class reports a positive count (at least 1).

- [ ] **Step 4: Run the route tests from Task 3 — they should now pass**

Run: `python3 -m pytest tests/web/test_routes_home.py -v`
Expected: Both tests pass — the template now renders `landing_film` fields, so "Chungking Express", "1994", "Wong Kar-wai", "102 min", and the backdrop URL all appear in the response body.

- [ ] **Step 5: Smoke-test the dev server**

Run the dev server in one terminal: `python3 app.py`

In another terminal (or via browser preview): load `http://127.0.0.1:5000/` and verify:
- Bebas Neue title visible (if blocked by network, will fall back to Arial Narrow)
- Backdrop image loads (or fallback pool backdrop if DB is empty)
- "Pick Another →" and "See this film ↗" buttons present
- Reload the page — different film picks render (when DB is populated)

Kill the dev server.

- [ ] **Step 6: Prepare commit (DO NOT commit)**

Show the user the diff. Suggested command:
```bash
git add templates/home.html static/css/output.css
git commit -m "feat(landing): rewrite home.html as Criterion-style film spotlight"
```

---

## Task 6: Responsive + motion validation sweep

**Files:**
- None modified; verification only.

- [ ] **Step 1: Start dev server**

Run: `python3 app.py`

- [ ] **Step 2: Desktop visual check (≥ 1280px)**

Load `/` in a browser at ≥ 1280px width. Verify:
- Navbar at top, transparent, over the backdrop
- `RANDOM · NO SIGN-UP` rotated label on the left edge
- Kicker `● YOUR RANDOM FILM` above title
- Film title dominates center in Bebas Neue (or fallback if Bebas blocked)
- Metadata line `{year} · {director} · {runtime}` below title
- White `PICK ANOTHER →` button + ghost `SEE THIS FILM ↗` button side by side
- `Film still: ...` credit in bottom-right corner
- Ken Burns: watch 30+ seconds — backdrop slowly zooms/shifts

- [ ] **Step 3: Tablet visual check (~768px)**

Open DevTools device toolbar, switch to iPad or set to 768×1024. Verify:
- Side label still shows (breakpoint is <768px so exactly 768 is desktop)
- Layout intact

Then bump to 767px. Verify:
- Side label disappears
- Title scales down smoothly
- CTAs stack vertically, full-width up to 320px

- [ ] **Step 4: Mobile visual check (~375px)**

Set to iPhone SE (375×667). Verify:
- Title ~48px (readable, not overflowing)
- CTAs stack vertically, centered
- Credit corner text at 8px
- No horizontal scroll

- [ ] **Step 5: Reduced-motion check**

In DevTools: Command palette (`Cmd+Shift+P`) → type "prefers-reduced-motion" → "Emulate CSS media feature prefers-reduced-motion" → choose `reduce`.

Reload page. Verify:
- Ken Burns animation stops (backdrop is static)
- Content fade-up on load is instant (elements appear immediately)

- [ ] **Step 6: Primary CTA functional test**

With DB populated: click `Pick Another →`. Verify:
- Submits CSRF form
- Navigates to a `/movie/<tconst>` page (existing behavior)

- [ ] **Step 7: Secondary CTA functional test**

Click `See this film ↗`. Verify:
- Navigates to `/movie/<tconst-of-currently-displayed-film>`
- That movie's detail page renders

- [ ] **Step 8: Fallback path functional test**

In a dev shell, temporarily break the query (easiest: rename `movie_projection` to `movie_projection_tmp` in MySQL, OR stop the aiomysql pool, OR patch `fetch_random_landing_film` to return None via an env var — pick whichever is easiest in your env).

Reload `/`. Verify:
- One of the three fallback films renders (Chungking Express, 2001, or In the Mood for Love)
- Different reloads show different fallback picks (random)

Restore the table/pool after testing.

- [ ] **Step 9: Keyboard accessibility check**

From the fresh-loaded page, press Tab repeatedly. Focus should move through:
1. "Skip to content" link (visible on focus)
2. Navbar brand
3. Navbar search icon
4. Navbar Pick pill
5. Navbar theme toggle
6. Navbar Log In link
7. `Pick Another →` (visible focus ring)
8. `See this film ↗` (visible focus ring)

Pressing Enter on either CTA actions it.

- [ ] **Step 10: Console + server-log clean check**

With DevTools Console open, reload `/` 5 times. Verify:
- No console errors (404, CSS parse, JS exceptions)
- Server terminal shows no exceptions (only normal GET / 200 lines)

- [ ] **Step 11: Run full test suite**

Kill the dev server.

Run: `python3 -m pytest tests/ -q --no-header 2>&1 | tail -5`
Expected: No regressions; total test count ≥ previous baseline + 8 new tests (7 from Task 2 + 2 from Task 3).

- [ ] **Step 12: Run black format check**

Run: `venv/bin/python3 -m black --check --line-length 100 movies/landing_film_service.py nextreel/web/routes/movies.py tests/movies/test_landing_film_service.py tests/web/test_routes_home.py`
Expected: `All done! ✨ 🍰 ✨` with `N files would be left unchanged.`

- [ ] **Step 13: Final commit preparation (DO NOT commit)**

If any of the validation steps surfaced issues and you made fixes, prepare a polish commit:

```bash
git add <fixed-files>
git commit -m "fix(landing): address validation-sweep issues"
```

Otherwise, no additional commit is needed — Tasks 1–5 cover all the code changes.

---

## Self-Review Checklist

After all tasks complete, verify:

- [ ] **Spec coverage** — each row in the "Design Decisions Summary" table of the spec has at least one corresponding task:
  - Single full-viewport frame → Task 5 (CSS `height: 100vh; overflow: hidden`)
  - Random film from projection → Task 2 (picker) + Task 3 (wire into route)
  - TMDb backdrop → Task 2 (`WHERE ... LIKE 'https://image.tmdb.org/%'`)
  - Bebas Neue 120px title → Task 1 (token) + Task 4 (`.landing-title`) + Task 5 (Google Fonts link)
  - Kicker / metadata / CTAs / side label / credit → Task 4 (CSS) + Task 5 (markup)
  - Ken Burns + fade-up + reduced-motion → Task 4 (keyframes + existing global rule)
  - Fallback pool → Task 3 (`_LANDING_FALLBACK_POOL`)
  - Responsive `< 768px` → Task 4 (media query)
- [ ] **Type consistency** — the dict shape `{tconst, title, year, director, runtime, backdrop_url}` is identical across `fetch_random_landing_film` (Task 2), `_LANDING_FALLBACK_POOL` (Task 3), and template rendering (Task 5). No field name drift.
- [ ] **No placeholders** — every code block has real content; no "TBD" / "similar to earlier" references.
- [ ] **Out-of-scope untouched** — navbar, routes other than `/`, projection_read_service, projection_repository all unchanged.

---

## Out of Scope (explicit non-goals)

- No changes to `templates/navbar_modern.html` (landing uses the existing navbar unchanged)
- No changes to other route modules (`navigation.py`, `account.py`, `search.py`, etc.)
- No changes to `projection_read_service.py`, `projection_repository.py`, or any projection-enrichment code
- No new route — `/` continues to be `home()` in `movies.py`
- No database schema changes
- No new tests for CSS / responsive (validated manually in Task 6 — CSS is not TDD territory)
- No weekly curated rotation, no press quotes, no laurels, no manifesto sections
