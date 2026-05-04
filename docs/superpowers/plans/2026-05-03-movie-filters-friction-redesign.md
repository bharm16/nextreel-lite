# Movie Filters Friction Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce friction in the movie filter drawer by replacing number inputs with chip+slider controls, adding live result counts, and surfacing the existing filter persistence — without changing the drawer's placement or breaking the existing filter form contract.

**Architecture:** Pure UI changes for chips (chips update sliders which write to existing hidden form inputs — server sees the same payload it does today). Three small backend changes: a new read-only `/api/filter_count` endpoint, defaults table swapped to `Any`, and a context processor extended to inject saved defaults into every template render. JS is rewritten to drop the obsolete accordion code and add chip↔slider sync, debounced count fetch, and modified-state diffing.

**Tech Stack:** Quart (async Flask), Jinja templates, vanilla JS (no framework), Tailwind CSS (`input.css` → `output.css`), pytest-asyncio for tests.

**Companion spec:** [docs/superpowers/specs/2026-05-03-movie-filters-friction-redesign-design.md](../specs/2026-05-03-movie-filters-friction-redesign-design.md)

---

## File Structure

**Create:**
- `tests/web/test_filter_count_endpoint.py` — tests for new `/api/filter_count` endpoint
- `tests/web/test_default_filters_context.py` — tests for context processor injection of `default_filters`

**Modify:**
- `infra/filter_normalizer.py` — change defaults in `default_filter_state()` to "Any" baseline
- `tests/infra/test_filter_normalizer.py` — update expectations for new defaults
- `nextreel/web/routes/navigation.py` — add `/api/filter_count` route handler
- `nextreel/web/routes/auth.py` — extend `inject_csrf_token` context processor with `default_filters`
- `templates/_filter_form.html` — full rebuild around chip + slider components, status row, scope toggles at top
- `templates/movie.html` — add `<script type="application/json" id="default-filters-data">` JSON island
- `static/js/filter-drawer.js` — remove accordion building; new code for chip↔slider sync, count fetch, modified diff, save toast
- `static/css/input.css` — chip variants, dual-handle slider, status row, sticky count footer, save toast

---

## Pre-flight checks

Before starting any task, verify the dev environment is healthy.

- [ ] **Step 1: Confirm tests pass on baseline**

Run: `python3 -m pytest tests/infra/test_filter_normalizer.py tests/web/ -v`
Expected: all pass. If anything is red, stop and investigate before proceeding.

- [ ] **Step 2: Confirm dev server starts**

Run: `python3 app.py`
Expected: server starts, listens on http://127.0.0.1:5000. Stop the server (Ctrl-C) once confirmed.

- [ ] **Step 3: Confirm Tailwind builds**

Run: `npm run build-css`
Expected: completes without error, `static/css/output.css` is regenerated.

---

## Task 1: Backend — Change default filter values to "Any"

**Files:**
- Modify: `infra/filter_normalizer.py:23-36` (the `default_filter_state` function)
- Modify: `tests/infra/test_filter_normalizer.py` (update existing assertions, add new ones)

**Why:** The current OOTB experience filters out roughly 60% of TMDb's quality content (foreign films) and most movies (≥7 IMDb, narrow vote band). Per the spec, new sessions should open with "Any" baselines so first-time users see broad discovery.

- [ ] **Step 1: Write the failing test for new defaults**

Add to `tests/infra/test_filter_normalizer.py`:

```python
def test_default_filter_state_uses_any_baselines():
    """OOTB defaults should be permissive ('Any') for new users."""
    state = default_filter_state()
    # IMDb score: full 1.0–10.0 range
    assert state["imdb_score_min"] == 1.0
    assert state["imdb_score_max"] == 10.0
    # Vote count: 0–2M (full sweep)
    assert state["num_votes_min"] == 0
    assert state["num_votes_max"] == 2000000
    # Language: any (was "en")
    assert state["language"] == "any"
    # Year: 1900–current (unchanged)
    assert state["year_min"] == 1900
    # Scope toggles unchanged
    assert state["exclude_watched"] is True
    assert state["exclude_watchlist"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infra/test_filter_normalizer.py::test_default_filter_state_uses_any_baselines -v`
Expected: FAIL — assertions fire on old defaults (`imdb_score_min == 7.0`, `language == "en"`, etc.)

- [ ] **Step 3: Update `default_filter_state` to new baselines**

Edit `infra/filter_normalizer.py:23-36` so the function reads:

```python
def default_filter_state(current_year: int | None = None) -> FilterState:
    year = current_year or utcnow().year
    return {
        "year_min": 1900,
        "year_max": year,
        "imdb_score_min": 1.0,
        "imdb_score_max": 10.0,
        "num_votes_min": 0,
        "num_votes_max": 2000000,
        "language": "any",
        "genres_selected": [],
        "exclude_watched": True,
        "exclude_watchlist": True,
    }
```

- [ ] **Step 4: Run the new test, verify it passes**

Run: `python3 -m pytest tests/infra/test_filter_normalizer.py::test_default_filter_state_uses_any_baselines -v`
Expected: PASS

- [ ] **Step 5: Run the full normalizer test file to catch downstream breakage**

Run: `python3 -m pytest tests/infra/test_filter_normalizer.py -v`
Expected: All pass. If any test that asserts on `default_filter_state()` results now fails because it was implicitly relying on old defaults, update the test to reflect the new baseline.

- [ ] **Step 6: Run any test that uses `criteria_from_filters` or `filters_from_criteria`**

Run: `python3 -m pytest tests/ -k "filter" -v`
Expected: All pass. Investigate and fix any failure caused by the default change.

- [ ] **Step 7: Stop. Do not commit.**

Per project preference, the user commits manually. Leave the working tree dirty for review.

---

## Task 2: Backend — Add `/api/filter_count` endpoint

**Files:**
- Create: `tests/web/test_filter_count_endpoint.py`
- Modify: `nextreel/web/routes/navigation.py` (add new route + register in `__all__`)
- Modify: `nextreel/application/movie_service.py` and `movies/candidate_store.py` (add `count_matching` method)

**Why:** Powers the live count badge in the new drawer footer. Reads from the same candidate pool cache that `/filtered_movie` uses, so warm filter combinations are essentially free.

- [ ] **Step 1: Write the failing test for the basic happy path**

Create `tests/web/test_filter_count_endpoint.py`:

```python
"""Tests for the POST /api/filter_count endpoint."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_filter_count_returns_int(client_with_movie_manager):
    """Endpoint returns an integer count for a valid filter payload."""
    client, _movie_manager = client_with_movie_manager
    csrf_token = await _get_csrf_token(client)

    response = await client.post(
        "/api/filter_count",
        form={
            "csrf_token": csrf_token,
            "year_min": "2010",
            "year_max": "2020",
            "imdb_score_min": "7.0",
            "imdb_score_max": "10.0",
            "num_votes_min": "0",
            "num_votes_max": "2000000",
            "language": "any",
            "exclude_watched": "off",
            "exclude_watchlist": "off",
        },
    )
    assert response.status_code == 200
    payload = await response.get_json()
    assert "count" in payload
    assert isinstance(payload["count"], int)
    assert payload["count"] >= 0


async def test_filter_count_rejects_invalid_filters(client_with_movie_manager):
    """Invalid filter values return 400 with errors, no count."""
    client, _movie_manager = client_with_movie_manager
    csrf_token = await _get_csrf_token(client)

    response = await client.post(
        "/api/filter_count",
        form={
            "csrf_token": csrf_token,
            "year_min": "9999",  # Out of range
            "year_max": "2020",
            "imdb_score_min": "7.0",
            "imdb_score_max": "10.0",
            "num_votes_min": "0",
            "num_votes_max": "2000000",
            "language": "any",
        },
    )
    assert response.status_code == 400
    payload = await response.get_json()
    assert payload["ok"] is False
    assert "errors" in payload


async def test_filter_count_requires_csrf(client_with_movie_manager):
    """Endpoint enforces CSRF like other state-shaped POSTs."""
    client, _movie_manager = client_with_movie_manager
    response = await client.post(
        "/api/filter_count",
        form={
            "year_min": "2010",
            "year_max": "2020",
            "imdb_score_min": "7.0",
            "imdb_score_max": "10.0",
            "num_votes_min": "0",
            "num_votes_max": "2000000",
            "language": "any",
        },
    )
    assert response.status_code in (400, 403)


async def _get_csrf_token(client):
    """Hit the home page to seed a session and extract a CSRF token."""
    response = await client.get("/")
    body = await response.get_data(as_text=True)
    # Token surfaces in the rendered filter form's hidden input
    import re
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', body)
    assert match, "csrf_token hidden input not found in response body"
    return match.group(1)
```

If `client_with_movie_manager` does not exist as a fixture, check `tests/conftest.py` for an equivalent (e.g., `app_client`, `client`, `quart_client`) and adapt. If no equivalent exists, model the new fixture on the pattern used in `tests/web/test_app.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_filter_count_endpoint.py -v`
Expected: FAIL with 404 (route doesn't exist) or fixture errors. If fixture errors, fix Step 1 to use the right fixture before continuing.

- [ ] **Step 3: Implement the endpoint**

Add to `nextreel/web/routes/navigation.py`, immediately after `filtered_movie_endpoint` (around line 215, before the `__all__` list):

```python
@bp.route("/api/filter_count", methods=["POST"])
@csrf_required
@rate_limited("filter_count")
@with_timeout(_REQUEST_TIMEOUT)
async def filter_count_endpoint():
    """Return the count of movies matching the submitted filter payload.

    Read-only counterpart to /filtered_movie — same form schema, just
    returns {count: int} instead of loading a movie.
    """
    movie_manager = _services().movie_manager
    state = _current_state()
    form_data = await request.form
    filters: FilterState = normalize_filters(form_data)
    validation_errors = validate_filters(filters)

    if validation_errors:
        return jsonify({"ok": False, "errors": validation_errors}), 400

    try:
        count = await movie_manager.count_matching_movies(
            state,
            filters,
            legacy_session=_legacy_session(),
        )
    except Exception:
        logger.exception(
            "filter_count failed for session_id=%s correlation_id=%s",
            state.session_id,
            g.correlation_id,
        )
        return jsonify({"ok": False, "errors": {"_": "count_unavailable"}}), 500

    return jsonify({"count": int(count)})
```

- [ ] **Step 4: Add the new endpoint to `__all__`**

In `nextreel/web/routes/navigation.py`, update the `__all__` list at the end of the file to include `"filter_count_endpoint"`:

```python
__all__ = [
    "filter_count_endpoint",
    "filtered_movie_endpoint",
    "next_movie",
    "previous_movie",
]
```

- [ ] **Step 5: Implement `MovieManager.count_matching_movies`**

In `nextreel/application/movie_service.py` (find `MovieManager`), add a new async method that returns the count of movies matching the supplied filters. Use the existing `candidate_filter_pool_cache` machinery if available, falling back to a direct count.

```python
async def count_matching_movies(
    self,
    state,
    filters,
    *,
    legacy_session=None,
) -> int:
    """Return the number of movies matching the given filter payload.

    Reads from the candidate filter pool cache when warm; falls back to
    a direct candidate count when cold. Does not mutate state.
    """
    criteria = criteria_from_filters(filters)
    return await self._candidate_store.count_matching(criteria)
```

If `_candidate_store` does not expose `count_matching`, add it. Look at `movies/candidate_store.py` — find the existing fetch query and add a parallel `count_matching(criteria)` method that runs `SELECT COUNT(*)` against `movie_candidates` with the same `WHERE` clause. Use the existing `MovieQueryBuilder` machinery.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/web/test_filter_count_endpoint.py -v`
Expected: PASS for all three tests.

- [ ] **Step 7: Run the full web test suite for regressions**

Run: `python3 -m pytest tests/web/ -v`
Expected: All pass.

- [ ] **Step 8: Stop. Do not commit.**

---

## Task 3: Backend — Inject `default_filters` into template context

**Files:**
- Create: `tests/web/test_default_filters_context.py`
- Modify: `nextreel/web/routes/auth.py:44-62` (the `inject_csrf_token` context processor)

**Why:** The client computes the "Modified" indicator by diffing the current form state against saved defaults. Rendering defaults as a JSON island in the page avoids a per-page-load round-trip and keeps the diff entirely client-side.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_default_filters_context.py`:

```python
"""Tests for the default_filters injection into template context."""

import json
import pytest

pytestmark = pytest.mark.asyncio


async def test_default_filters_present_for_anonymous_user(client_with_movie_manager):
    """Anonymous users see system defaults rendered as JSON in the page."""
    client, _movie_manager = client_with_movie_manager
    response = await client.get("/")
    body = await response.get_data(as_text=True)

    # JSON island should be present and parseable
    import re
    match = re.search(
        r'<script[^>]+id="default-filters-data"[^>]*>(.*?)</script>',
        body,
        re.DOTALL,
    )
    assert match, "default-filters-data JSON island missing from rendered page"

    payload = json.loads(match.group(1))
    # Anonymous defaults are the system "Any" baselines
    assert payload["imdb_score_min"] == 1.0
    assert payload["language"] == "any"
    assert payload["exclude_watched"] is True
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python3 -m pytest tests/web/test_default_filters_context.py -v`
Expected: FAIL — JSON island doesn't exist yet.

- [ ] **Step 3: Extend the context processor to add `default_filters`**

In `nextreel/web/routes/auth.py:44-62`, modify `inject_csrf_token` to also fetch and inject `default_filters`. Add the import at the top of the file alongside the existing `user_preferences` import:

```python
from infra.filter_normalizer import default_filter_state
```

Then update the function body to fetch saved defaults for logged-in users and fall back to system defaults for anonymous users:

```python
@bp.app_context_processor
async def inject_csrf_token():
    state = getattr(g, "navigation_state", None)
    user_id = getattr(state, "user_id", None) if state else None
    oauth_config = getattr(current_app, "oauth_config", {})
    posthog_config = getattr(current_app, "posthog_config", None) or {}

    # Fetch saved defaults for logged-in users; fall back to system defaults.
    default_filters = None
    if user_id:
        try:
            db_pool = _services().movie_manager.db_pool
        except Exception:
            db_pool = None
        if db_pool is not None:
            try:
                default_filters = await user_preferences.get_default_filters(db_pool, user_id)
            except Exception:
                default_filters = None
    if default_filters is None:
        default_filters = default_filter_state()

    return {
        "csrf_token": _get_csrf_token,
        "current_user_id": user_id,
        "current_filters": (getattr(state, "filters", None) or {}),
        "current_year": _current_year(),
        "default_filters": default_filters,
        "is_watched": getattr(g, "is_watched", False),
        "is_in_watchlist": getattr(g, "is_in_watchlist", False),
        "google_enabled": oauth_config.get("google_enabled", False),
        "user_avatar_info": user_avatar_info,
        "posthog_enabled": bool(posthog_config.get("enabled")),
        "posthog_project_key": posthog_config.get("project_key", ""),
        "posthog_api_host": posthog_config.get("api_host", "/ph"),
    }
```

Note: `inject_csrf_token` becomes `async` if it isn't already — check the `@bp.app_context_processor` signature and the existing `await _load_current_user_once()` pattern in `inject_account_context` for reference.

- [ ] **Step 4: Add the JSON island to `templates/movie.html`**

Edit `templates/movie.html`. Just before the `<script src="...filter-drawer.js">` tag (around line 103), insert:

```html
<script type="application/json" id="default-filters-data">{{ default_filters | tojson }}</script>
```

Also add the same block to `templates/home.html` and any other template that opens the filter drawer, if those exist. Search for `filter-drawer.js` to find them: `grep -rn "filter-drawer.js" templates/`.

- [ ] **Step 5: Run the test, verify it passes**

Run: `python3 -m pytest tests/web/test_default_filters_context.py -v`
Expected: PASS.

- [ ] **Step 6: Verify no other tests broke**

Run: `python3 -m pytest tests/web/ -v`
Expected: All pass.

- [ ] **Step 7: Stop. Do not commit.**

---

## Task 4: Frontend — Rebuild `_filter_form.html`

**Files:**
- Modify: `templates/_filter_form.html` (complete rewrite)
- Modify: `templates/movie.html` (drawer footer gets count badge + toast container)

**Why:** Replace 6 number inputs + 16 vertical toggles + 17-option select with chip+slider hybrids, scope toggles at top, and a status row. The new structure removes the need for the accordion code in `filter-drawer.js`.

- [ ] **Step 1: Replace the entire contents of `templates/_filter_form.html`**

Overwrite with:

```html
{# Shared filter form fields — included by the filter drawer in movie.html.
   Expects: current_filters (dict), current_year (int), default_filters (dict),
            validation_errors (dict, default {}), current_user_id (optional),
            genres_notice (optional string).
   Does NOT include: <form> tag, CSRF input, submit/reset buttons. #}
{% set validation_errors = validation_errors or {} %}

{# Status row — visibility toggled by JS based on current vs default diff. #}
<div class="filter-status-row hidden" data-filter-status-row>
  <span class="filter-status-modified">Modified</span>
  <span class="filter-status-sep">·</span>
  <button type="button" class="filter-status-reset" data-filter-reset-link>
    {% if current_user_id and default_filters %}Reset to my defaults{% else %}Reset to defaults{% endif %}
  </button>
</div>

{# Scope toggles — pinned at top, auth-only. #}
{% if current_user_id %}
<section class="filter-scope-toggles">
  <input type="hidden" name="exclude_watched" value="off">
  <div class="filter-toggle-row">
    <span class="filter-toggle-label">Exclude watched</span>
    <label class="filter-toggle-switch">
      <input type="checkbox" id="excludeWatched" name="exclude_watched" value="on"
             {% if current_filters.get('exclude_watched', true) %}checked{% endif %}>
      <span class="filter-toggle-track"></span>
    </label>
  </div>
  <input type="hidden" name="exclude_watchlist" value="off">
  <div class="filter-toggle-row">
    <span class="filter-toggle-label">Hide movies in my watchlist</span>
    <label class="filter-toggle-switch">
      <input type="checkbox" id="excludeWatchlist" name="exclude_watchlist" value="on"
             {% if current_filters.get('exclude_watchlist', true) %}checked{% endif %}>
      <span class="filter-toggle-track"></span>
    </label>
  </div>
</section>
{% endif %}

{# Genres — chip cloud with explicit "All" #}
<section class="filter-section" aria-labelledby="genres-heading">
  <h2 id="genres-heading" class="filter-section-heading">Genres</h2>
  {% if genres_notice %}<p class="drawer-error">{{ genres_notice }}</p>{% endif %}
  {% set selected_genres = current_filters.get('genres_selected', []) %}
  {% set all_selected = selected_genres|length == 0 %}
  <div class="filter-chip-row" data-filter-chips="genres">
    <button type="button" class="filter-chip {% if all_selected %}is-active{% endif %}"
            data-genre-chip="__all__" aria-pressed="{{ 'true' if all_selected else 'false' }}">All</button>
    {% set genres = ['Action','Adventure','Animation','Biography','Comedy','Crime','Documentary','Drama','Fantasy','Horror','Musical','Sci-Fi','Sport','Thriller','War','Western'] %}
    {% for g in genres %}
    <button type="button" class="filter-chip {% if not all_selected and g in selected_genres %}is-active{% endif %}"
            data-genre-chip="{{ g }}"
            aria-pressed="{{ 'true' if (not all_selected and g in selected_genres) else 'false' }}">{{ g }}</button>
    {% endfor %}
  </div>
  {# Hidden inputs — JS keeps these in sync with chip state. #}
  <div data-genre-hidden-inputs>
    {% for g in selected_genres %}
    <input type="hidden" name="genres[]" value="{{ g }}">
    {% endfor %}
  </div>
</section>

{# Year — chips + dual-handle slider. #}
<section class="filter-section" aria-labelledby="year-heading">
  <h2 id="year-heading" class="filter-section-heading">Year</h2>
  <div class="filter-chip-row" data-filter-chips="year">
    <button type="button" class="filter-chip" data-range-chip data-min="1900" data-max="{{ current_year }}">Any</button>
    <button type="button" class="filter-chip" data-range-chip data-min="2020" data-max="2029">2020s</button>
    <button type="button" class="filter-chip" data-range-chip data-min="2010" data-max="2019">2010s</button>
    <button type="button" class="filter-chip" data-range-chip data-min="2000" data-max="2009">2000s</button>
    <button type="button" class="filter-chip" data-range-chip data-min="1990" data-max="1999">90s</button>
    <button type="button" class="filter-chip" data-range-chip data-min="1900" data-max="1989">Classic</button>
  </div>
  <div class="filter-slider"
       data-dual-slider
       data-field-min="year_min"
       data-field-max="year_max"
       data-bound-min="1900"
       data-bound-max="{{ current_year }}"
       data-step="1">
    <div class="filter-slider-track"></div>
    <input type="range" min="1900" max="{{ current_year }}" step="1"
           value="{{ current_filters.get('year_min', 1900) }}" data-slider-handle="min" aria-label="Earliest year">
    <input type="range" min="1900" max="{{ current_year }}" step="1"
           value="{{ current_filters.get('year_max', current_year) }}" data-slider-handle="max" aria-label="Latest year">
    <div class="filter-slider-label" data-slider-label></div>
  </div>
  {# Hidden inputs the form actually submits — JS syncs from sliders. #}
  <input type="hidden" name="year_min" value="{{ current_filters.get('year_min', 1900) }}" data-hidden-min>
  <input type="hidden" name="year_max" value="{{ current_filters.get('year_max', current_year) }}" data-hidden-max>
  {% if validation_errors.get('year_min') %}<p class="filter-field-error">{{ validation_errors.get('year_min') }}</p>{% endif %}
  {% if validation_errors.get('year_max') %}<p class="filter-field-error">{{ validation_errors.get('year_max') }}</p>{% endif %}
</section>

{# IMDb Score — chips + dual-handle slider. #}
<section class="filter-section" aria-labelledby="score-heading">
  <h2 id="score-heading" class="filter-section-heading">IMDb Score</h2>
  <div class="filter-chip-row" data-filter-chips="imdb">
    <button type="button" class="filter-chip" data-range-chip data-min="1.0" data-max="10.0">Any</button>
    <button type="button" class="filter-chip" data-range-chip data-min="6.0" data-max="10.0">≥6</button>
    <button type="button" class="filter-chip" data-range-chip data-min="7.0" data-max="10.0">≥7</button>
    <button type="button" class="filter-chip" data-range-chip data-min="7.5" data-max="10.0">≥7.5</button>
    <button type="button" class="filter-chip" data-range-chip data-min="8.0" data-max="10.0">≥8</button>
  </div>
  <div class="filter-slider"
       data-dual-slider
       data-field-min="imdb_score_min"
       data-field-max="imdb_score_max"
       data-bound-min="1.0"
       data-bound-max="10.0"
       data-step="0.1">
    <div class="filter-slider-track"></div>
    <input type="range" min="1.0" max="10.0" step="0.1"
           value="{{ current_filters.get('imdb_score_min', 1.0) }}" data-slider-handle="min" aria-label="Minimum IMDb score">
    <input type="range" min="1.0" max="10.0" step="0.1"
           value="{{ current_filters.get('imdb_score_max', 10.0) }}" data-slider-handle="max" aria-label="Maximum IMDb score">
    <div class="filter-slider-label" data-slider-label></div>
  </div>
  <input type="hidden" name="imdb_score_min" value="{{ current_filters.get('imdb_score_min', 1.0) }}" data-hidden-min>
  <input type="hidden" name="imdb_score_max" value="{{ current_filters.get('imdb_score_max', 10.0) }}" data-hidden-max>
  {% if validation_errors.get('imdb_score_min') %}<p class="filter-field-error">{{ validation_errors.get('imdb_score_min') }}</p>{% endif %}
  {% if validation_errors.get('imdb_score_max') %}<p class="filter-field-error">{{ validation_errors.get('imdb_score_max') }}</p>{% endif %}
</section>

{# Language — top chips + "More" disclosure. #}
<section class="filter-section" aria-labelledby="language-heading">
  <h2 id="language-heading" class="filter-section-heading">Language</h2>
  {% set top_langs = [('any','Any'),('en','English'),('es','Spanish'),('fr','French'),('ja','Japanese'),('ko','Korean'),('hi','Hindi')] %}
  {% set more_langs = [('de','German'),('it','Italian'),('pt','Portuguese'),('ru','Russian'),('zh','Chinese'),('ar','Arabic'),('tr','Turkish'),('nl','Dutch'),('sv','Swedish'),('pl','Polish')] %}
  {% set selected_lang = current_filters.get('language', 'any') %}
  <div class="filter-chip-row" data-filter-chips="language">
    {% for code, label in top_langs %}
    <button type="button" class="filter-chip {% if selected_lang == code %}is-active{% endif %}"
            data-lang-chip="{{ code }}"
            aria-pressed="{{ 'true' if selected_lang == code else 'false' }}">{{ label }}</button>
    {% endfor %}
    <button type="button" class="filter-chip-more" data-lang-more aria-expanded="false">More languages ▾</button>
  </div>
  <div class="filter-chip-row hidden" data-lang-more-row>
    {% for code, label in more_langs %}
    <button type="button" class="filter-chip {% if selected_lang == code %}is-active{% endif %}"
            data-lang-chip="{{ code }}"
            aria-pressed="{{ 'true' if selected_lang == code else 'false' }}">{{ label }}</button>
    {% endfor %}
  </div>
  <input type="hidden" name="language" value="{{ selected_lang }}" data-hidden-language>
</section>

{# Vote Count — chips + dual-handle slider. #}
<section class="filter-section" aria-labelledby="votes-heading">
  <h2 id="votes-heading" class="filter-section-heading">Vote Count</h2>
  <div class="filter-chip-row" data-filter-chips="votes">
    <button type="button" class="filter-chip" data-range-chip data-min="0" data-max="2000000">Any</button>
    <button type="button" class="filter-chip" data-range-chip data-min="500000" data-max="2000000">Mainstream</button>
    <button type="button" class="filter-chip" data-range-chip data-min="50000" data-max="499999">Mixed</button>
    <button type="button" class="filter-chip" data-range-chip data-min="1000" data-max="49999">Hidden gem</button>
  </div>
  <div class="filter-slider"
       data-dual-slider
       data-field-min="num_votes_min"
       data-field-max="num_votes_max"
       data-bound-min="0"
       data-bound-max="2000000"
       data-step="1000">
    <div class="filter-slider-track"></div>
    <input type="range" min="0" max="2000000" step="1000"
           value="{{ current_filters.get('num_votes_min', 0) }}" data-slider-handle="min" aria-label="Minimum vote count">
    <input type="range" min="0" max="2000000" step="1000"
           value="{{ current_filters.get('num_votes_max', 2000000) }}" data-slider-handle="max" aria-label="Maximum vote count">
    <div class="filter-slider-label" data-slider-label></div>
  </div>
  <input type="hidden" name="num_votes_min" value="{{ current_filters.get('num_votes_min', 0) }}" data-hidden-min>
  <input type="hidden" name="num_votes_max" value="{{ current_filters.get('num_votes_max', 2000000) }}" data-hidden-max>
  {% if validation_errors.get('num_votes_min') %}<p class="filter-field-error">{{ validation_errors.get('num_votes_min') }}</p>{% endif %}
  {% if validation_errors.get('num_votes_max') %}<p class="filter-field-error">{{ validation_errors.get('num_votes_max') }}</p>{% endif %}
</section>
```

- [ ] **Step 2: Update the drawer footer in `templates/movie.html`** to add the live count badge above the Apply button

Edit `templates/movie.html:86-100` (the `.filter-drawer-footer` block) to insert a count badge before the Apply button:

```html
<div class="filter-drawer-footer">
  <div class="filter-count-badge" data-filter-count-badge>
    <span data-filter-count-text>~ matches</span>
  </div>
  <button type="submit" form="drawerFilterForm" id="drawerApplyBtn" class="filter-apply-btn" aria-busy="false">
    <svg class="h-4 w-4 hidden animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10" opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
    <span>Apply Filters</span>
  </button>
  <button type="reset" form="drawerFilterForm" id="drawerResetBtn" class="filter-reset-btn">Reset</button>
  {% if current_user_id %}
  <button type="submit"
          form="drawerFilterForm"
          formaction="{{ url_for('main.account_filters_save') }}"
          class="filter-save-default-btn"
          data-save-default-btn>
    Save these as my defaults
  </button>
  {% endif %}
</div>
<div class="filter-toast hidden" data-filter-toast role="status" aria-live="polite"></div>
```

- [ ] **Step 3: Restart the dev server and verify the page loads without server-side errors**

Run: `python3 app.py` (in background or another terminal)
Then: `curl -s http://127.0.0.1:5000/ -o /dev/null -w "%{http_code}\n"`
Expected: `200`

If the page returns 500, check the server log — most likely a Jinja template error from a missing variable. Stop the server before continuing.

- [ ] **Step 4: Stop. Do not commit.**

---

## Task 5: Frontend — Add CSS for chips, sliders, status row, count badge, toast

**Files:**
- Modify: `static/css/input.css` (append new component styles)

**Why:** Style the new structural elements introduced in Task 4. Keep additions self-contained at the bottom of the file so they're easy to find and remove if rolled back.

- [ ] **Step 1: Append new component styles to `static/css/input.css`**

At the bottom of `static/css/input.css`, add:

```css
/* ── Filter redesign components ───────────────────────── */

/* Status row */
.filter-status-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  margin-bottom: 0.75rem;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 0.5rem;
  font-size: 0.85rem;
}
.filter-status-modified { font-weight: 600; }
.filter-status-sep { opacity: 0.5; }
.filter-status-reset {
  background: none;
  border: none;
  color: var(--accent, #4ea1ff);
  text-decoration: underline;
  cursor: pointer;
  font-size: inherit;
  padding: 0;
}
.filter-status-reset:hover { opacity: 0.8; }

/* Scope toggles container */
.filter-scope-toggles {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding-bottom: 0.75rem;
  margin-bottom: 0.75rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

/* Section */
.filter-section { margin-bottom: 1.25rem; }
.filter-section-heading {
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.75;
  margin-bottom: 0.5rem;
}

/* Chip rows */
.filter-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 0.5rem;
}

/* Chip */
.filter-chip,
.filter-chip-more {
  display: inline-flex;
  align-items: center;
  padding: 0.4rem 0.85rem;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  background: transparent;
  color: inherit;
  font-size: 0.85rem;
  cursor: pointer;
  transition: background 0.12s ease, border-color 0.12s ease;
  min-height: 36px; /* mobile tap target */
}
.filter-chip:hover,
.filter-chip-more:hover { background: rgba(255, 255, 255, 0.06); }
.filter-chip:focus-visible,
.filter-chip-more:focus-visible {
  outline: 2px solid var(--accent, #4ea1ff);
  outline-offset: 2px;
}
.filter-chip.is-active {
  background: var(--accent, #4ea1ff);
  border-color: var(--accent, #4ea1ff);
  color: #fff;
}

/* Dual-handle slider — two range inputs overlaid on a shared track */
.filter-slider {
  position: relative;
  height: 36px;
  margin: 0.25rem 0 0.75rem;
}
.filter-slider-track {
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 4px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 2px;
  transform: translateY(-50%);
  pointer-events: none;
}
.filter-slider input[type="range"] {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 36px;
  background: transparent;
  -webkit-appearance: none;
  appearance: none;
  pointer-events: none; /* enabled per-thumb below */
}
.filter-slider input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  pointer-events: auto;
  width: 18px;
  height: 18px;
  background: var(--accent, #4ea1ff);
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid rgba(0, 0, 0, 0.2);
}
.filter-slider input[type="range"]::-moz-range-thumb {
  pointer-events: auto;
  width: 18px;
  height: 18px;
  background: var(--accent, #4ea1ff);
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid rgba(0, 0, 0, 0.2);
}
.filter-slider-label {
  margin-top: 0.4rem;
  font-size: 0.8rem;
  opacity: 0.8;
}

/* Count badge */
.filter-count-badge {
  font-size: 0.85rem;
  opacity: 0.85;
  padding: 0.35rem 0;
  text-align: center;
}
.filter-count-badge.is-loading { opacity: 0.5; }

/* Save-default toast */
.filter-toast {
  position: fixed;
  bottom: 1.5rem;
  left: 50%;
  transform: translateX(-50%);
  padding: 0.65rem 1.1rem;
  background: rgba(0, 0, 0, 0.8);
  color: #fff;
  border-radius: 0.5rem;
  font-size: 0.9rem;
  z-index: 1000;
  pointer-events: none;
}
.filter-toast.is-visible { animation: filter-toast-fade 2.2s ease forwards; }
@keyframes filter-toast-fade {
  0% { opacity: 0; transform: translate(-50%, 8px); }
  10%, 80% { opacity: 1; transform: translate(-50%, 0); }
  100% { opacity: 0; transform: translate(-50%, 0); }
}

.hidden { display: none !important; }
```

- [ ] **Step 2: Rebuild Tailwind output**

Run: `npm run build-css`
Expected: completes, regenerates `static/css/output.css`.

- [ ] **Step 3: Visual smoke test**

Run: `python3 app.py`
Visit http://127.0.0.1:5000/ in a browser, navigate to a movie detail page, click the FILTERS tab.
Expected: chips render as rounded pills, sliders show dual handles, scope toggles appear at the top for logged-in users, count badge shows in the footer (text may be empty until JS lands in Task 6).

If anything looks broken structurally (overflow, overlap), fix in `input.css` and re-run `npm run build-css`. Don't fix interactivity yet — that's Task 6.

- [ ] **Step 4: Stop the dev server. Do not commit.**

---

## Task 6: Frontend — Rewrite `filter-drawer.js` (chip↔slider sync, drop accordion)

**Files:**
- Modify: `static/js/filter-drawer.js` (full rewrite of the body, keep the drawer open/close logic)

**Why:** The accordion-building code in the current file is obsolete after Task 4 (chip/slider markup is already compact). Replace it with chip↔slider sync, single-select chips for language, and "More languages" disclosure.

- [ ] **Step 1: Replace `filter-drawer.js` contents**

Overwrite `static/js/filter-drawer.js` with:

```javascript
/**
 * Filter drawer interactions.
 *
 * Wires:
 *  - Drawer open/close (tab, backdrop, ESC).
 *  - Chip ↔ slider sync for range filters (Year, IMDb, Vote Count).
 *  - Genre chip cloud with explicit "All".
 *  - Single-select language chips with "More languages" disclosure.
 *  - Live count badge fetch (debounced) — see attachLiveCount().
 *  - Modified-state diff against rendered default_filters — see attachModifiedDiff().
 *  - Save-default toast — see attachSaveToast().
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    var drawer = document.getElementById("filterDrawer");
    var tab = document.getElementById("filterDrawerTab");
    var closeBtn = document.getElementById("filterDrawerClose");
    var backdrop = document.getElementById("filterDrawerBackdrop");
    var form = document.getElementById("drawerFilterForm");
    if (!drawer || !tab || !form || !closeBtn || !backdrop) return;

    attachOpenClose(drawer, tab, closeBtn, backdrop);
    attachGenreChips(form);
    attachRangeChipsAndSliders(form);
    attachLanguageChips(form);
    attachLanguageMore(form);
    attachLiveCount(form);
    attachModifiedDiff(form);
    attachSaveToast(form);
  }

  // ── Open / close ────────────────────────────────────────
  function attachOpenClose(drawer, tab, closeBtn, backdrop) {
    function open() {
      drawer.classList.add("is-open");
      backdrop.classList.add("is-visible");
      tab.setAttribute("aria-expanded", "true");
      document.body.classList.add("filter-drawer-open");
    }
    function close() {
      drawer.classList.remove("is-open");
      backdrop.classList.remove("is-visible");
      tab.setAttribute("aria-expanded", "false");
      document.body.classList.remove("filter-drawer-open");
    }
    tab.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    backdrop.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer.classList.contains("is-open")) close();
    });
  }

  // ── Genre chips ─────────────────────────────────────────
  function attachGenreChips(form) {
    var allGenres = [
      "Action","Adventure","Animation","Biography","Comedy","Crime","Documentary",
      "Drama","Fantasy","Horror","Musical","Sci-Fi","Sport","Thriller","War","Western"
    ];
    var hiddenContainer = form.querySelector("[data-genre-hidden-inputs]");
    var chipRow = form.querySelector('[data-filter-chips="genres"]');
    if (!hiddenContainer || !chipRow) return;

    function syncHidden() {
      // Use replaceChildren() to clear safely (no innerHTML, no XSS surface)
      hiddenContainer.replaceChildren();
      var allActive = chipRow.querySelector('[data-genre-chip="__all__"]').classList.contains("is-active");
      if (allActive) {
        form.dispatchEvent(new CustomEvent("filter:changed"));
        return;
      }
      chipRow.querySelectorAll('[data-genre-chip]').forEach(function (c) {
        var v = c.getAttribute("data-genre-chip");
        if (v === "__all__") return;
        if (c.classList.contains("is-active")) {
          var input = document.createElement("input");
          input.type = "hidden";
          input.name = "genres[]";
          input.value = v;
          hiddenContainer.appendChild(input);
        }
      });
      form.dispatchEvent(new CustomEvent("filter:changed"));
    }

    chipRow.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-genre-chip]");
      if (!btn) return;
      var value = btn.getAttribute("data-genre-chip");
      if (value === "__all__") {
        chipRow.querySelectorAll('[data-genre-chip]').forEach(function (c) {
          c.classList.remove("is-active");
          c.setAttribute("aria-pressed", "false");
        });
        btn.classList.add("is-active");
        btn.setAttribute("aria-pressed", "true");
      } else {
        var allChip = chipRow.querySelector('[data-genre-chip="__all__"]');
        allChip.classList.remove("is-active");
        allChip.setAttribute("aria-pressed", "false");
        btn.classList.toggle("is-active");
        btn.setAttribute("aria-pressed", btn.classList.contains("is-active") ? "true" : "false");

        var activeSpecifics = chipRow.querySelectorAll('[data-genre-chip].is-active:not([data-genre-chip="__all__"])');
        if (activeSpecifics.length === 0 || activeSpecifics.length === allGenres.length) {
          chipRow.querySelectorAll('[data-genre-chip]').forEach(function (c) {
            c.classList.remove("is-active");
            c.setAttribute("aria-pressed", "false");
          });
          allChip.classList.add("is-active");
          allChip.setAttribute("aria-pressed", "true");
        }
      }
      syncHidden();
    });
  }

  // ── Range chips + sliders ───────────────────────────────
  function attachRangeChipsAndSliders(form) {
    form.querySelectorAll("[data-dual-slider]").forEach(function (sliderRoot) {
      var minHandle = sliderRoot.querySelector('[data-slider-handle="min"]');
      var maxHandle = sliderRoot.querySelector('[data-slider-handle="max"]');
      var label = sliderRoot.querySelector("[data-slider-label]");
      var hiddenMin = sliderRoot.parentElement.querySelector("[data-hidden-min]");
      var hiddenMax = sliderRoot.parentElement.querySelector("[data-hidden-max]");
      var section = sliderRoot.closest("section");
      var chipRow = section ? section.querySelector("[data-filter-chips]") : null;
      var step = parseFloat(sliderRoot.getAttribute("data-step")) || 1;
      var isFloat = step < 1;

      function fmt(v) {
        return isFloat ? Number(v).toFixed(1) : Math.round(Number(v)).toString();
      }
      function updateLabel() {
        if (label) label.textContent = fmt(minHandle.value) + " – " + fmt(maxHandle.value);
      }
      function clamp() {
        var mn = parseFloat(minHandle.value);
        var mx = parseFloat(maxHandle.value);
        if (mn > mx) {
          minHandle.value = String(Math.min(mn, mx));
          maxHandle.value = String(Math.max(mn, mx));
        }
      }
      function syncHidden() {
        hiddenMin.value = minHandle.value;
        hiddenMax.value = maxHandle.value;
      }
      function deactivateChips() {
        if (!chipRow) return;
        chipRow.querySelectorAll(".filter-chip").forEach(function (c) {
          c.classList.remove("is-active");
          c.setAttribute("aria-pressed", "false");
        });
      }
      function syncChipsToValues() {
        if (!chipRow) return;
        var mn = minHandle.value, mx = maxHandle.value;
        chipRow.querySelectorAll("[data-range-chip]").forEach(function (c) {
          var cm = c.getAttribute("data-min");
          var cmax = c.getAttribute("data-max");
          if (cm === mn && cmax === mx) {
            c.classList.add("is-active");
            c.setAttribute("aria-pressed", "true");
          } else {
            c.classList.remove("is-active");
            c.setAttribute("aria-pressed", "false");
          }
        });
      }

      [minHandle, maxHandle].forEach(function (h) {
        h.addEventListener("input", function () {
          clamp();
          updateLabel();
          syncHidden();
          deactivateChips();
        });
        h.addEventListener("change", function () {
          form.dispatchEvent(new CustomEvent("filter:changed"));
        });
      });

      if (chipRow) {
        chipRow.addEventListener("click", function (e) {
          var btn = e.target.closest("[data-range-chip]");
          if (!btn) return;
          minHandle.value = btn.getAttribute("data-min");
          maxHandle.value = btn.getAttribute("data-max");
          updateLabel();
          syncHidden();
          chipRow.querySelectorAll("[data-range-chip]").forEach(function (c) {
            c.classList.remove("is-active");
            c.setAttribute("aria-pressed", "false");
          });
          btn.classList.add("is-active");
          btn.setAttribute("aria-pressed", "true");
          form.dispatchEvent(new CustomEvent("filter:changed"));
        });
      }

      updateLabel();
      syncHidden();
      syncChipsToValues();
    });
  }

  // ── Language chips ─────────────────────────────────────
  function attachLanguageChips(form) {
    var hidden = form.querySelector("[data-hidden-language]");
    if (!hidden) return;
    form.querySelectorAll("[data-lang-chip]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var v = chip.getAttribute("data-lang-chip");
        hidden.value = v;
        form.querySelectorAll("[data-lang-chip]").forEach(function (c) {
          c.classList.remove("is-active");
          c.setAttribute("aria-pressed", "false");
        });
        chip.classList.add("is-active");
        chip.setAttribute("aria-pressed", "true");
        form.dispatchEvent(new CustomEvent("filter:changed"));
      });
    });
  }

  function attachLanguageMore(form) {
    var btn = form.querySelector("[data-lang-more]");
    var row = form.querySelector("[data-lang-more-row]");
    if (!btn || !row) return;
    btn.addEventListener("click", function () {
      var hidden = row.classList.toggle("hidden");
      btn.setAttribute("aria-expanded", hidden ? "false" : "true");
    });
  }

  // ── Stubs filled in by Task 7/8 ─────────────────────────
  function attachLiveCount(_form) { /* implemented in Task 7 */ }
  function attachModifiedDiff(_form) { /* implemented in Task 8 */ }
  function attachSaveToast(_form) { /* implemented in Task 8 */ }
})();
```

- [ ] **Step 2: Manual smoke test**

Run: `python3 app.py`
Visit http://127.0.0.1:5000/, navigate to a movie page, open the FILTERS drawer.

Verify:
- Chips highlight when tapped.
- Tapping a Year chip (e.g., "2010s") moves both slider handles to 2010 and 2019; the label updates.
- Dragging a slider handle deselects all chips for that range.
- Tapping a Language chip selects it and deselects others; the "More languages" button reveals/hides additional chips.
- Tapping a specific genre deselects "All". Tapping "All" reselects only "All".
- The Apply button still loads a new movie (existing behavior).

If anything misbehaves, check the browser console and fix.

- [ ] **Step 3: Stop the dev server. Do not commit.**

---

## Task 7: Frontend — Live count fetch

**Files:**
- Modify: `static/js/filter-drawer.js` (replace the `attachLiveCount` stub with full implementation)

**Why:** Powers the count badge in the footer. Debounces ~150ms after the last change, cancels in-flight requests, falls back gracefully on errors.

- [ ] **Step 1: Replace the `attachLiveCount` stub with the real implementation**

In `static/js/filter-drawer.js`, replace the line `function attachLiveCount(_form) { /* implemented in Task 7 */ }` with:

```javascript
function attachLiveCount(form) {
  var badge = document.querySelector("[data-filter-count-badge]");
  var text = document.querySelector("[data-filter-count-text]");
  if (!badge || !text) return;

  var debounceMs = 150;
  var timer = null;
  var inflight = null;

  function fmt(n) {
    if (n >= 1000) return "~" + (Math.round(n / 10) * 10).toLocaleString() + " matches";
    return n.toLocaleString() + " matches";
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(fetchCount, debounceMs);
  }

  function fetchCount() {
    if (inflight) inflight.abort();
    inflight = new AbortController();
    badge.classList.add("is-loading");
    text.textContent = "Counting…";

    var data = new FormData(form);
    fetch("/api/filter_count", {
      method: "POST",
      body: data,
      signal: inflight.signal,
      credentials: "same-origin",
    })
      .then(function (r) {
        if (!r.ok) throw new Error("count_failed");
        return r.json();
      })
      .then(function (j) {
        text.textContent = fmt(j.count);
        badge.classList.remove("is-loading");
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        badge.classList.remove("is-loading");
        text.textContent = "Count unavailable";
      });
  }

  form.addEventListener("filter:changed", schedule);
  // Also fire on initial open so the badge isn't blank.
  document.getElementById("filterDrawerTab").addEventListener("click", schedule, { once: true });
}
```

- [ ] **Step 2: Manual verification**

Run: `python3 app.py`
Open the drawer. Tap several chips. Confirm:
- The badge updates ~150ms after the last tap.
- The number is reasonable (>0 for Any, <Any for stricter filters).
- Rapidly tapping multiple chips coalesces to one request (check the Network tab — only the final request should complete).
- If the server is killed mid-request, the badge shows "Count unavailable" rather than spinning forever.

- [ ] **Step 3: Stop. Do not commit.**

---

## Task 8: Frontend — Modified-state diff, reset link, save toast

**Files:**
- Modify: `static/js/filter-drawer.js` (replace the `attachModifiedDiff` and `attachSaveToast` stubs)

**Why:** Surfaces the existing persistence (status row + reset link) and confirms saves with a toast. All client-side; no backend changes.

- [ ] **Step 1: Replace the `attachModifiedDiff` stub**

In `static/js/filter-drawer.js`, replace `function attachModifiedDiff(_form) { /* implemented in Task 8 */ }` with:

```javascript
function attachModifiedDiff(form) {
  var statusRow = form.querySelector("[data-filter-status-row]");
  var resetLink = form.querySelector("[data-filter-reset-link]");
  var defaultsScript = document.getElementById("default-filters-data");
  if (!statusRow || !resetLink || !defaultsScript) return;

  var defaults = {};
  try { defaults = JSON.parse(defaultsScript.textContent || "{}"); } catch (e) { return; }

  function readCurrent() {
    var fd = new FormData(form);
    return {
      year_min: parseInt(fd.get("year_min") || "0", 10),
      year_max: parseInt(fd.get("year_max") || "0", 10),
      imdb_score_min: parseFloat(fd.get("imdb_score_min") || "0"),
      imdb_score_max: parseFloat(fd.get("imdb_score_max") || "0"),
      num_votes_min: parseInt(fd.get("num_votes_min") || "0", 10),
      num_votes_max: parseInt(fd.get("num_votes_max") || "0", 10),
      language: fd.get("language") || "any",
      genres_selected: fd.getAll("genres[]").slice().sort(),
      exclude_watched: fd.getAll("exclude_watched").indexOf("on") >= 0,
      exclude_watchlist: fd.getAll("exclude_watchlist").indexOf("on") >= 0,
    };
  }

  function eq(cur, def) {
    var defGenres = (def.genres_selected || []).slice().sort();
    return (
      cur.year_min === def.year_min &&
      cur.year_max === def.year_max &&
      cur.imdb_score_min === def.imdb_score_min &&
      cur.imdb_score_max === def.imdb_score_max &&
      cur.num_votes_min === def.num_votes_min &&
      cur.num_votes_max === def.num_votes_max &&
      cur.language === def.language &&
      JSON.stringify(cur.genres_selected) === JSON.stringify(defGenres) &&
      cur.exclude_watched === def.exclude_watched &&
      cur.exclude_watchlist === def.exclude_watchlist
    );
  }

  function refresh() {
    var modified = !eq(readCurrent(), defaults);
    statusRow.classList.toggle("hidden", !modified);
  }

  function applyDefaultsToForm() {
    setRange(form, "year_min", "year_max", defaults.year_min, defaults.year_max);
    setRange(form, "imdb_score_min", "imdb_score_max", defaults.imdb_score_min, defaults.imdb_score_max);
    setRange(form, "num_votes_min", "num_votes_max", defaults.num_votes_min, defaults.num_votes_max);

    var langHidden = form.querySelector("[data-hidden-language]");
    if (langHidden) {
      langHidden.value = defaults.language || "any";
      form.querySelectorAll("[data-lang-chip]").forEach(function (c) {
        var active = c.getAttribute("data-lang-chip") === langHidden.value;
        c.classList.toggle("is-active", active);
        c.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    var hiddenContainer = form.querySelector("[data-genre-hidden-inputs]");
    var chipRow = form.querySelector('[data-filter-chips="genres"]');
    if (hiddenContainer && chipRow) {
      hiddenContainer.replaceChildren();
      var defGenres = defaults.genres_selected || [];
      var allActive = defGenres.length === 0;
      chipRow.querySelectorAll("[data-genre-chip]").forEach(function (c) {
        var v = c.getAttribute("data-genre-chip");
        var active = (v === "__all__" && allActive) || (v !== "__all__" && defGenres.indexOf(v) >= 0);
        c.classList.toggle("is-active", active);
        c.setAttribute("aria-pressed", active ? "true" : "false");
      });
      defGenres.forEach(function (g) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = "genres[]";
        input.value = g;
        hiddenContainer.appendChild(input);
      });
    }

    var ew = form.querySelector("#excludeWatched");
    if (ew) ew.checked = !!defaults.exclude_watched;
    var ewl = form.querySelector("#excludeWatchlist");
    if (ewl) ewl.checked = !!defaults.exclude_watchlist;

    form.dispatchEvent(new CustomEvent("filter:changed"));
    refresh();
  }

  function setRange(form, minName, maxName, mnVal, mxVal) {
    var minHidden = form.querySelector('[name="' + minName + '"][data-hidden-min]');
    var maxHidden = form.querySelector('[name="' + maxName + '"][data-hidden-max]');
    if (minHidden) minHidden.value = mnVal;
    if (maxHidden) maxHidden.value = mxVal;
    var section = minHidden ? minHidden.closest("section") : null;
    if (!section) return;
    var minHandle = section.querySelector('[data-slider-handle="min"]');
    var maxHandle = section.querySelector('[data-slider-handle="max"]');
    if (minHandle) minHandle.value = mnVal;
    if (maxHandle) maxHandle.value = mxVal;
    var label = section.querySelector("[data-slider-label]");
    if (label && minHandle && maxHandle) {
      var step = parseFloat(section.querySelector("[data-dual-slider]").getAttribute("data-step")) || 1;
      var isFloat = step < 1;
      var fmt = function (v) {
        return isFloat ? Number(v).toFixed(1) : Math.round(Number(v)).toString();
      };
      label.textContent = fmt(minHandle.value) + " – " + fmt(maxHandle.value);
    }
    section.querySelectorAll("[data-range-chip]").forEach(function (c) {
      var match = c.getAttribute("data-min") === String(mnVal) && c.getAttribute("data-max") === String(mxVal);
      c.classList.toggle("is-active", match);
      c.setAttribute("aria-pressed", match ? "true" : "false");
    });
  }

  resetLink.addEventListener("click", applyDefaultsToForm);
  form.addEventListener("filter:changed", refresh);
  form.addEventListener("change", refresh);
  refresh();
}
```

- [ ] **Step 2: Replace the `attachSaveToast` stub**

Replace `function attachSaveToast(_form) { /* implemented in Task 8 */ }` with:

```javascript
function attachSaveToast(form) {
  var saveBtn = form.querySelector("[data-save-default-btn]");
  var toast = document.querySelector("[data-filter-toast]");
  if (!saveBtn || !toast) return;

  // Submit save-defaults via fetch so we can show a toast without losing the
  // user's current filter selection (which a full POST navigation would clear).
  saveBtn.addEventListener("click", function (e) {
    e.preventDefault();
    var data = new FormData(form);
    fetch(saveBtn.getAttribute("formaction"), {
      method: "POST",
      body: data,
      credentials: "same-origin",
    })
      .then(function (r) {
        if (!r.ok) throw new Error("save_failed");
        showToast("Saved as your defaults");
      })
      .catch(function () {
        showToast("Couldn't save — try again");
      });
  });

  function showToast(msg) {
    toast.textContent = msg;
    toast.classList.remove("hidden");
    toast.classList.remove("is-visible");
    void toast.offsetWidth; // restart CSS animation
    toast.classList.add("is-visible");
    setTimeout(function () {
      toast.classList.add("hidden");
      toast.classList.remove("is-visible");
    }, 2400);
  }
}
```

- [ ] **Step 3: Confirm `account_filters_save` accepts fetch (no redirect-only)**

Read `nextreel/web/routes/account.py:241-` to inspect `account_filters_save`. If it returns a redirect (`return redirect(...)`), the fetch will follow the redirect — which is fine — but we may want it to return a JSON body when called via fetch. For minimal change, accept the redirect; the toast fires off the HTTP 200 status.

If the route does anything that requires a full page load (sets a flash, expects to render a confirmation page), update the test plan in Task 9 manual verification to confirm.

- [ ] **Step 4: Manual verification**

Run: `python3 app.py`. Sign in if not already. Open the filter drawer.

Verify:
- The status row stays hidden when current matches saved defaults.
- Tapping a chip that changes the values reveals `Modified · Reset to my defaults`.
- Clicking "Reset to my defaults" snaps everything back to the rendered defaults and the status row hides again.
- Clicking "Save these as my defaults" shows a toast and the status row hides (because current now equals defaults).

For an anonymous session: confirm the reset link reads "Reset to defaults" and resets to system defaults.

- [ ] **Step 5: Stop. Do not commit.**

---

## Task 9: Final verification — full test suite + manual smoke

**Files:** none modified.

- [ ] **Step 1: Full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 2: Lint and format**

Run: `black . --line-length 100`
Run: `flake8 . --exclude=venv,node_modules`
Expected: no errors. Fix any black-reformatted files (commit-ready).

- [ ] **Step 3: Tailwind rebuild**

Run: `npm run build-css`
Expected: completes; `static/css/output.css` regenerated.

- [ ] **Step 4: Manual end-to-end smoke (anonymous user)**

Run: `python3 app.py`. In a fresh incognito window:
- Visit `/`. Click into a movie.
- Open the FILTERS drawer.
- Confirm the count badge populates.
- Tap "2010s" chip → year slider snaps; count updates; chip stays selected.
- Drag the IMDb slider → IMDb chips deselect; count updates.
- Tap multiple genres → "All" deselects; count updates.
- Tap "More languages" → extra chips appear.
- Click Apply → drawer closes, new movie loads.
- Re-open drawer. Status row should show "Modified" (current ≠ system defaults).
- Click "Reset to defaults" → status row hides, controls reset.

- [ ] **Step 5: Manual end-to-end smoke (logged-in user)**

In a regular browser, sign in. Repeat the flow plus:
- Apply a non-default filter set.
- Click "Save these as my defaults" → toast appears; status row hides.
- Refresh the page, re-open drawer → status row stays hidden (current = saved defaults).
- Modify a filter → status row reads "Modified · Reset to my defaults".
- Click reset link → snaps back to saved defaults.

- [ ] **Step 6: Mobile viewport check**

In dev tools, switch to a mobile viewport (e.g., iPhone 12). Re-open the drawer.
- Confirm chips are tap-friendly (≥36px tall).
- Confirm sliders are still usable (both handles draggable).
- Confirm the count badge and Apply button stay visible without horizontal scroll.

- [ ] **Step 7: Stop. Hand off to user for review and commit.**

The user commits manually. Do not run `git commit`. Summarize the change in your handoff message: which files changed, what test coverage was added, and any open items the user should validate before merge.

---

## Open items to flag in handoff

- **Vote Count chip thresholds** (Mainstream ≥500k, Mixed 50k–500k, Hidden gem 1k–50k) are guesses. If `movie_candidates` data shows a different distribution, retune in `templates/_filter_form.html` (Task 4 markup).
- **Top 6 language list** picked by intuition. If analytics show a different top 6, swap in the `top_langs` template variable.
- **Default change** is real product impact. Existing logged-in users with saved defaults are unaffected. Anonymous and brand-new users will see the new "Any" baseline.
