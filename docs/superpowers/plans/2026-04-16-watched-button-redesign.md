# Watched Button Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relocate the "Mark as Watched" button from its standalone block on the movie detail page into the sticky bottom navigation bar (between Previous and Next), restyle it as a text-only peer matching the existing Prev/Next vocabulary, and decouple its JS from hardcoded Tailwind class strings.

**Architecture:** Template change in `templates/movie_card.html` relocates the existing `<form data-watched-toggle-form>` from the body into the `.movie-nav-bar`. CSS additions in `static/css/input.css` and `static/css/tokens.css` introduce a `.nav-btn-watched` component and a `--color-watched` token. JS simplification in `static/js/movie-card.js` removes hardcoded className swaps in favor of a `data-watched-state` attribute that CSS styles. No routes, workers, or server-side code changes — the existing `/watched/add/{tconst}` and `/watched/remove/{tconst}` endpoints and JSON response are untouched.

**Tech Stack:** Quart (Jinja2 templates), vanilla JS, Tailwind CSS 3.4 with custom CSS tokens, pytest for structural/static-asset regression tests.

**Spec:** `docs/superpowers/specs/2026-04-16-watched-button-redesign-design.md`

---

## File Structure

**Files created:**
- `tests/web/test_watched_button_redesign.py` — structural regression tests for the new token, CSS rule, template placement, and JS refactor

**Files modified:**
- `static/css/tokens.css` — add `--color-watched` token in all four theme declarations (`:root`, `@media (prefers-color-scheme: dark) :root`, `[data-theme="light"]`, `[data-theme="dark"]`)
- `static/css/input.css` — add `.nav-btn-watched` base + state rules near the existing `.nav-btn-prev` / `.nav-btn-next` block (~line 377), and a responsive label-swap rule in the existing `@media (max-width: 640px)` block (~line 1589)
- `static/css/output.css` — regenerated via `npm run build-css` at the end (not hand-edited)
- `templates/movie_card.html` — delete the standalone `<div class="mt-3">` watched block (lines 77–116), add a new form inside `.movie-nav-bar` between the Previous and Next forms
- `static/js/movie-card.js` — simplify the watched IIFE: remove `watchedClassName` / `unwatchedClassName` literals, stop mutating `button.className`, keep dataset + innerHTML + aria swaps

**No other files change.** Routes, workers, database, and session code are untouched. Existing tests in `tests/web/test_static_script_boundaries.py` and `tests/web/test_routes_navigation.py` must continue to pass.

---

## Task 1: Add `--color-watched` token to `tokens.css`

**Files:**
- Create: `tests/web/test_watched_button_redesign.py`
- Modify: `static/css/tokens.css`

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_watched_button_redesign.py` with:

```python
"""Structural regression tests for the 2026-04-16 watched button redesign.

Asserts that the redesigned control lives in the sticky nav bar, uses the new
--color-watched token, carries the .nav-btn-watched class, and that the JS no
longer hardcodes Tailwind utility strings.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tokens_css() -> str:
    return (ROOT / "static" / "css" / "tokens.css").read_text(encoding="utf-8")


def test_color_watched_token_defined_in_light_root():
    css = _tokens_css()
    # Light-mode :root block
    root_block = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
    assert root_block, "could not locate :root block in tokens.css"
    assert "--color-watched:" in root_block.group(1), (
        "--color-watched token must be defined in the :root (light) block"
    )


def test_color_watched_token_defined_in_dark_media_query():
    css = _tokens_css()
    # prefers-color-scheme dark block
    dark_media = re.search(
        r"@media \(prefers-color-scheme: dark\)\s*\{[^{}]*:root\s*\{([^}]*)\}",
        css,
        re.DOTALL,
    )
    assert dark_media, "could not locate dark prefers-color-scheme :root block"
    assert "--color-watched:" in dark_media.group(1), (
        "--color-watched token must be defined in the dark prefers-color-scheme block"
    )


def test_color_watched_token_defined_in_explicit_light_theme():
    css = _tokens_css()
    light_theme = re.search(r'\[data-theme="light"\]\s*\{([^}]*)\}', css, re.DOTALL)
    assert light_theme, 'could not locate [data-theme="light"] block'
    assert "--color-watched:" in light_theme.group(1), (
        '--color-watched token must be defined in the [data-theme="light"] block'
    )


def test_color_watched_token_defined_in_explicit_dark_theme():
    css = _tokens_css()
    dark_theme = re.search(r'\[data-theme="dark"\]\s*\{([^}]*)\}', css, re.DOTALL)
    assert dark_theme, 'could not locate [data-theme="dark"] block'
    assert "--color-watched:" in dark_theme.group(1), (
        '--color-watched token must be defined in the [data-theme="dark"] block'
    )
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/web/test_watched_button_redesign.py -v`
Expected: 4 failures — `--color-watched` not present in any of the four blocks yet.

- [ ] **Step 3: Add the token to all four theme blocks in `static/css/tokens.css`**

In the `:root` block (light mode, before `color-scheme: light;`), add:

```css
  --color-watched: #5a8a3c;
```

In the `@media (prefers-color-scheme: dark) :root` block (before `color-scheme: dark;`), add:

```css
    --color-watched: #9bc97b;
```

In the `[data-theme="light"]` block (before `color-scheme: light;`), add:

```css
  --color-watched: #5a8a3c;
```

In the `[data-theme="dark"]` block (before `color-scheme: dark;`), add:

```css
  --color-watched: #9bc97b;
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `python3 -m pytest tests/web/test_watched_button_redesign.py -v`
Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_watched_button_redesign.py static/css/tokens.css
git commit -m "feat(css): add --color-watched design token"
```

---

## Task 2: Add `.nav-btn-watched` rules to `input.css`

**Files:**
- Modify: `static/css/input.css` — add rules near line 377 (after `.nav-btn-prev` / `.nav-btn-next` block) and in the `@media (max-width: 640px)` block (~line 1589)
- Modify: `tests/web/test_watched_button_redesign.py` — add CSS rule assertions

- [ ] **Step 1: Write the failing tests**

Append these test functions to `tests/web/test_watched_button_redesign.py`:

```python
def _input_css() -> str:
    return (ROOT / "static" / "css" / "input.css").read_text(encoding="utf-8")


def test_nav_btn_watched_base_rule_exists():
    css = _input_css()
    assert ".nav-btn-watched" in css, (
        ".nav-btn-watched class must be defined in input.css"
    )


def test_nav_btn_watched_uses_color_watched_token_for_watched_state():
    css = _input_css()
    # Match: form[data-watched-state="watched"] .nav-btn-watched { ... color: var(--color-watched) ... }
    pattern = re.compile(
        r'form\[data-watched-state="watched"\]\s+\.nav-btn-watched\s*\{[^}]*color:\s*var\(--color-watched\)',
        re.DOTALL,
    )
    assert pattern.search(css), (
        "watched-state rule must set color to var(--color-watched)"
    )


def test_nav_btn_watched_unwatched_state_uses_muted_text_token():
    css = _input_css()
    pattern = re.compile(
        r'form\[data-watched-state="unwatched"\]\s+\.nav-btn-watched\s*\{[^}]*color:\s*var\(--color-text-muted\)',
        re.DOTALL,
    )
    assert pattern.search(css), (
        "unwatched-state rule must set color to var(--color-text-muted)"
    )


def test_nav_btn_watched_prefix_hidden_on_mobile():
    css = _input_css()
    # The rule must live inside a @media (max-width: 640px) block
    media_block = re.search(
        r"@media \(max-width:\s*640px\)\s*\{(.*?)\n\s*\}\s*(?=@media|\Z)",
        css,
        re.DOTALL,
    )
    assert media_block, "could not locate @media (max-width: 640px) block"
    assert ".nav-btn-watched__prefix" in media_block.group(1), (
        ".nav-btn-watched__prefix must be hidden inside the 640px media query"
    )
    assert "display: none" in media_block.group(1), (
        "responsive prefix rule must use display: none"
    )
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/web/test_watched_button_redesign.py -v`
Expected: the 4 new tests fail. The 4 token tests from Task 1 still pass.

- [ ] **Step 3: Add the base + state rules to `static/css/input.css`**

Locate the existing rule block at approximately line 377 (the `.nav-btn-prev, .nav-btn-next { ... }` block and its associated hover/active/disabled rules). Immediately after line 394 (after `.nav-btn-prev:active, .nav-btn-next:active { transform: scale(0.97); }`), insert:

```css
  /* Mark Watched — sticky nav bar, peer to Prev/Next */
  .nav-btn-watched {
    font-size: 0.8rem; font-weight: 600;
    letter-spacing: 0.02em;
    cursor: pointer;
    background: none; border: none;
    font-family: var(--font-sans);
    display: inline-flex; align-items: center; gap: 0.35rem;
    padding: 0.4rem 0;
    transition: color var(--duration-normal) var(--easing-default), opacity var(--duration-normal) var(--easing-default);
  }
  .nav-btn-watched svg {
    width: 16px; height: 16px;
    stroke: currentColor; fill: none; stroke-width: 2;
  }
  form[data-watched-state="unwatched"] .nav-btn-watched {
    color: var(--color-text-muted);
  }
  form[data-watched-state="unwatched"] .nav-btn-watched:hover {
    color: var(--color-text);
  }
  form[data-watched-state="watched"] .nav-btn-watched {
    color: var(--color-watched);
  }
  form[data-watched-state="watched"] .nav-btn-watched:hover {
    opacity: 0.85;
  }
  .nav-btn-watched:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .nav-btn-watched:active {
    transform: scale(0.97);
  }
```

- [ ] **Step 4: Add the responsive label-swap rule**

Locate the existing `@media (max-width: 640px)` block at approximately line 1578 (containing `.movie-page-layout`, `.movie-nav-bar`, etc.). Immediately after the `.movie-nav-bar { padding: 0.5rem 1rem; }` line (~line 1589), insert:

```css
    .nav-btn-watched__prefix { display: none; }
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `python3 -m pytest tests/web/test_watched_button_redesign.py -v`
Expected: all 8 tests pass (4 token + 4 CSS rule).

- [ ] **Step 6: Commit**

```bash
git add tests/web/test_watched_button_redesign.py static/css/input.css
git commit -m "feat(css): add .nav-btn-watched component styles"
```

---

## Task 3: Relocate the watched form into `.movie-nav-bar`

**Files:**
- Modify: `templates/movie_card.html` — delete lines 77–116 (standalone block), insert new form inside `.movie-nav-bar`
- Modify: `tests/web/test_watched_button_redesign.py` — add template structure assertions

- [ ] **Step 1: Write the failing tests**

Append these test functions to `tests/web/test_watched_button_redesign.py`:

```python
def _movie_card_template() -> str:
    return (ROOT / "templates" / "movie_card.html").read_text(encoding="utf-8")


def test_standalone_watched_block_removed():
    html = _movie_card_template()
    # The old block was: <div class="mt-3"> ... <form data-watched-toggle-form ...
    # After redesign, data-watched-toggle-form must only appear inside .movie-nav-bar.
    # Check no <div class="mt-3"> wraps the watched form anywhere.
    pattern = re.compile(
        r'<div[^>]*class="[^"]*\bmt-3\b[^"]*"[^>]*>\s*\{%\s*if is_watched',
        re.DOTALL,
    )
    assert not pattern.search(html), (
        "standalone <div class='mt-3'> watched block must be removed"
    )


def test_watched_form_lives_inside_movie_nav_bar():
    html = _movie_card_template()
    # Find the <nav class="movie-nav-bar"> ... </nav> block and assert the
    # watched form is inside it.
    nav_block = re.search(
        r'<nav[^>]*class="movie-nav-bar"[^>]*>(.*?)</nav>',
        html,
        re.DOTALL,
    )
    assert nav_block, "could not locate <nav class='movie-nav-bar'> block"
    assert "data-watched-toggle-form" in nav_block.group(1), (
        "data-watched-toggle-form must live inside .movie-nav-bar"
    )


def test_watched_form_gated_by_current_user_id():
    html = _movie_card_template()
    nav_block = re.search(
        r'<nav[^>]*class="movie-nav-bar"[^>]*>(.*?)</nav>',
        html,
        re.DOTALL,
    )
    assert nav_block, "could not locate <nav class='movie-nav-bar'> block"
    # The watched form must be wrapped in {% if current_user_id %}
    inner = nav_block.group(1)
    assert "{% if current_user_id %}" in inner, (
        "watched form must be gated by {% if current_user_id %}"
    )
    # And that guard must precede data-watched-toggle-form
    guard_pos = inner.index("{% if current_user_id %}")
    form_pos = inner.index("data-watched-toggle-form")
    assert guard_pos < form_pos, (
        "{% if current_user_id %} must appear before the watched form"
    )


def test_watched_button_uses_nav_btn_watched_class():
    html = _movie_card_template()
    # The button inside the watched form must carry nav-btn-watched
    pattern = re.compile(
        r'data-watched-toggle-form[^>]*>.*?class="[^"]*\bnav-btn-watched\b[^"]*"',
        re.DOTALL,
    )
    assert pattern.search(html), (
        "watched toggle button must have the nav-btn-watched class"
    )


def test_watched_button_prefix_span_present():
    html = _movie_card_template()
    assert 'class="nav-btn-watched__prefix"' in html, (
        "label must include <span class='nav-btn-watched__prefix'>Mark as </span> for mobile truncation"
    )


def test_watched_form_retains_data_attributes_for_js():
    """JS selector hasn't changed — form must still expose the attributes the IIFE reads."""
    html = _movie_card_template()
    for attr in (
        "data-watched-toggle-form",
        "data-watched-state",
        "data-add-url",
        "data-remove-url",
    ):
        assert attr in html, f"form must retain {attr} for movie-card.js to hook in"
    # Button still needs data-watched-toggle-button
    assert "data-watched-toggle-button" in html
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/web/test_watched_button_redesign.py -v`
Expected: the 6 new tests fail (standalone block still present, form not in nav bar yet). All earlier tests still pass.

- [ ] **Step 3: Delete the standalone watched block from `templates/movie_card.html`**

Remove lines 77–116 of `templates/movie_card.html` (inclusive). The block to delete starts with `{% if current_user_id %}` on line 77 (immediately after the `<!-- Actions: Trailer + External Links -->` action-row `</div>` closes on line 75) and ends with `{% endif %}` on line 116, followed by an empty line.

The deleted block is exactly:

```jinja
    {% if current_user_id %}
    {% set watch_tconst = movie.tconst or movie.imdb_id %}
    <div class="mt-3">
      {% if is_watched %}
      <form method="POST"
            action="/watched/remove/{{ watch_tconst }}"
            class="inline"
            data-watched-toggle-form
            data-watched-state="watched"
            data-add-url="/watched/add/{{ watch_tconst }}"
            data-remove-url="/watched/remove/{{ watch_tconst }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <button type="submit"
                class="inline-flex items-center gap-1.5 rounded-full bg-green-600 px-4 py-2 text-xs font-semibold text-white hover:bg-green-700"
                data-watched-toggle-button
                aria-pressed="true">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>
          Watched
        </button>
      </form>
      {% else %}
      <form method="POST"
            action="/watched/add/{{ watch_tconst }}"
            class="inline"
            data-watched-toggle-form
            data-watched-state="unwatched"
            data-add-url="/watched/add/{{ watch_tconst }}"
            data-remove-url="/watched/remove/{{ watch_tconst }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <button type="submit"
                class="inline-flex items-center gap-1.5 rounded-full chip px-4 py-2 text-xs font-semibold text-body hover:opacity-80"
                data-watched-toggle-button
                aria-pressed="false">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
          Mark as Watched
        </button>
      </form>
      {% endif %}
    </div>
    {% endif %}
```

- [ ] **Step 4: Insert the new form inside `.movie-nav-bar`**

In `templates/movie_card.html`, locate the sticky nav bar section (was at line 267, now at roughly line 228 after the delete). The current nav bar looks like:

```jinja
  <nav class="movie-nav-bar" aria-label="Movie navigation">
    <form method="POST" action="/previous_movie" class="inline">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit" class="nav-btn-prev" {% if previous_count == 0 %}disabled{% endif %}>
        &larr; Previous
      </button>
    </form>
    <form method="POST" action="/next_movie" class="inline">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit" class="nav-btn-next">
        Next &rarr;
      </button>
    </form>
  </nav>
```

Replace the entire block (from `<nav class="movie-nav-bar"` through the closing `</nav>`) with:

```jinja
  <nav class="movie-nav-bar" aria-label="Movie navigation">
    <form method="POST" action="/previous_movie" class="inline">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit" class="nav-btn-prev" {% if previous_count == 0 %}disabled{% endif %}>
        &larr; Previous
      </button>
    </form>

    {% if current_user_id %}
    {% set watch_tconst = movie.tconst or movie.imdb_id %}
    <form method="POST"
          action="{% if is_watched %}/watched/remove/{{ watch_tconst }}{% else %}/watched/add/{{ watch_tconst }}{% endif %}"
          class="inline"
          data-watched-toggle-form
          data-watched-state="{% if is_watched %}watched{% else %}unwatched{% endif %}"
          data-add-url="/watched/add/{{ watch_tconst }}"
          data-remove-url="/watched/remove/{{ watch_tconst }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit"
              class="nav-btn-watched"
              data-watched-toggle-button
              aria-pressed="{% if is_watched %}true{% else %}false{% endif %}">
        {% if is_watched %}
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>
        Watched
        {% else %}
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
        <span class="nav-btn-watched__prefix">Mark as </span>Watched
        {% endif %}
      </button>
    </form>
    {% endif %}

    <form method="POST" action="/next_movie" class="inline">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit" class="nav-btn-next">
        Next &rarr;
      </button>
    </form>
  </nav>
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `python3 -m pytest tests/web/test_watched_button_redesign.py -v`
Expected: all tests pass (token + CSS + template — 14 total).

- [ ] **Step 6: Run the existing boundary and route tests to confirm no regression**

Run: `python3 -m pytest tests/web/test_static_script_boundaries.py tests/web/test_routes_navigation.py tests/web/test_route_view_contracts.py -v`
Expected: all existing tests still pass (especially `test_movie_card_template_delegates_browser_behavior_to_static_assets`).

- [ ] **Step 7: Commit**

```bash
git add tests/web/test_watched_button_redesign.py templates/movie_card.html
git commit -m "refactor(templates): relocate watched toggle into sticky nav bar"
```

---

## Task 4: Simplify `movie-card.js` — drop hardcoded Tailwind class strings

**Files:**
- Modify: `static/js/movie-card.js` — simplify the watched IIFE (lines 59–123)
- Modify: `tests/web/test_watched_button_redesign.py` — add JS structure assertions

- [ ] **Step 1: Write the failing tests**

Append these test functions to `tests/web/test_watched_button_redesign.py`:

```python
def _movie_card_js() -> str:
    return (ROOT / "static" / "js" / "movie-card.js").read_text(encoding="utf-8")


def test_movie_card_js_no_longer_hardcodes_tailwind_watched_classes():
    js = _movie_card_js()
    # The old implementation literal-embedded Tailwind utility strings like
    # "bg-green-600", "rounded-full", and "chip" in the JS for class swapping.
    # After the refactor, all styling lives in CSS — none of these should
    # appear in movie-card.js.
    for forbidden in ("bg-green-600", "bg-green-700", "rounded-full"):
        assert forbidden not in js, (
            f"{forbidden!r} must not appear in movie-card.js — styling belongs in CSS"
        )


def test_movie_card_js_does_not_mutate_button_classname():
    js = _movie_card_js()
    # No assignment to button.className anywhere in the file.
    assert "button.className" not in js, (
        "movie-card.js must not mutate button.className — "
        "styling is driven by data-watched-state on the form"
    )


def test_movie_card_js_still_toggles_data_watched_state():
    js = _movie_card_js()
    # The attribute-based state machine must remain.
    assert "dataset.watchedState" in js or 'dataset["watchedState"]' in js, (
        "movie-card.js must still toggle form.dataset.watchedState"
    )


def test_movie_card_js_still_updates_aria_pressed():
    js = _movie_card_js()
    assert 'setAttribute("aria-pressed"' in js, (
        "movie-card.js must continue to manage aria-pressed for a11y"
    )
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m pytest tests/web/test_watched_button_redesign.py -v`
Expected: the 4 new JS tests fail (`bg-green-600` and `button.className` still present in `movie-card.js`). All other tests still pass.

- [ ] **Step 3: Simplify the watched IIFE in `static/js/movie-card.js`**

Replace the entire watched IIFE — the block starting at line 59 with `(function () {` and ending at line 123 with `})();` (the one containing `var form = document.querySelector("[data-watched-toggle-form]")`) — with this rewritten version:

```js
(function () {
  var form = document.querySelector("[data-watched-toggle-form]");
  if (!form) return;

  var button = form.querySelector("[data-watched-toggle-button]");
  var csrfInput = form.querySelector('input[name="csrf_token"]');
  var status = document.getElementById("movie-status");
  var addUrl = form.dataset.addUrl;
  var removeUrl = form.dataset.removeUrl;

  var watchedMarkup =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>' +
    " Watched";
  var unwatchedMarkup =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>' +
    '<circle cx="12" cy="12" r="3"/></svg> ' +
    '<span class="nav-btn-watched__prefix">Mark as </span>Watched';

  function setWatchedState(isWatched) {
    form.dataset.watchedState = isWatched ? "watched" : "unwatched";
    form.action = isWatched ? removeUrl : addUrl;
    button.innerHTML = isWatched ? watchedMarkup : unwatchedMarkup;
    button.setAttribute("aria-pressed", isWatched ? "true" : "false");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!button || button.disabled) return;

    var isWatched = form.dataset.watchedState === "watched";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    if (status) {
      status.textContent = isWatched ? "Removing from watched..." : "Saving to watched...";
    }

    fetch(form.action, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-CSRFToken": csrfInput ? csrfInput.value : "",
      },
      credentials: "same-origin",
    }).then(function (response) {
      return response.json().catch(function () {
        return null;
      }).then(function (payload) {
        if (!response.ok || !payload || !payload.ok) {
          throw new Error("watched toggle failed");
        }
        return payload;
      });
    }).then(function (payload) {
      setWatchedState(Boolean(payload.is_watched));
      if (status) {
        status.textContent = payload.is_watched ? "Marked as watched." : "Removed from watched.";
      }
    }).catch(function (error) {
      console.error("Failed to update watched state:", error);
      if (status) {
        status.textContent = "Could not update watched status.";
      }
    }).finally(function () {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
  });
})();
```

Notes on what changed vs. the old IIFE:
- Removed `watchedClassName` and `unwatchedClassName` variables.
- Removed `button.className = ...` assignment in `setWatchedState`.
- `unwatchedMarkup` now embeds the `<span class="nav-btn-watched__prefix">Mark as </span>` element so the mobile CSS rule can hide it.
- `watchedMarkup` drops the `h-4 w-4` Tailwind classes on the SVG — CSS sizes it via `.nav-btn-watched svg`.
- Added `aria-hidden="true"` on the SVGs (decorative; the button text already conveys meaning).

- [ ] **Step 4: Run the tests and verify they pass**

Run: `python3 -m pytest tests/web/test_watched_button_redesign.py -v`
Expected: all 18 tests pass (4 token + 4 CSS + 6 template + 4 JS).

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `python3 -m pytest tests/ -q`
Expected: full suite passes, coverage threshold still met.

- [ ] **Step 6: Commit**

```bash
git add tests/web/test_watched_button_redesign.py static/js/movie-card.js
git commit -m "refactor(js): drop hardcoded Tailwind strings from watched toggle"
```

---

## Task 5: Rebuild Tailwind CSS and verify in browser

**Files:**
- Modify: `static/css/output.css` (regenerated by `npm run build-css`)

- [ ] **Step 1: Rebuild the compiled Tailwind CSS**

Run: `npm run build-css`
Expected: `npx tailwindcss` completes without errors. `static/css/output.css` is regenerated, now including the new `.nav-btn-watched` rule block and `--color-watched` token values. The old `bg-green-600` / `rounded-full` utility classes may be purged if they're no longer referenced anywhere in the project.

- [ ] **Step 2: Start the dev server**

Run: `python3 app.py`
Expected: Server starts on `http://127.0.0.1:5000` without errors. Watch the stdout for any template or CSS errors on first request.

- [ ] **Step 3: Manual browser verification — logged-out state**

Open `http://127.0.0.1:5000/` in a browser while logged out. Navigate to a movie detail page by clicking the hero or pressing Next.

Verify:
- The sticky bottom bar shows only **Previous** and **Next**, distributed edge-to-edge.
- No "Mark as Watched" label appears anywhere on the page.
- The body of the movie page (where the old green pill used to sit) shows no gap or empty container — flow is unbroken from action row straight to collection banner / watch providers.

- [ ] **Step 4: Manual browser verification — logged-in, unwatched state**

Log in as a test user. Navigate to any movie detail page.

Verify:
- The sticky bar shows three peers: `← Previous` on the left, `Mark as Watched` (with eye icon) in the middle, `Next →` on the right.
- The middle button's color matches `--color-text-muted` (same muted gray as Previous).
- Hover on the middle button transitions its color to `--color-text` (same hover as Previous).
- No outlined pill, no green background, no `rounded-full` chip.

- [ ] **Step 5: Manual browser verification — click to mark watched**

Click the middle button.

Verify:
- Button becomes temporarily disabled (opacity 0.4) during the request.
- On success, the button text changes to `Watched` (with check icon), color shifts to the sage green defined by `--color-watched`.
- `aria-pressed` flips to `"true"` (confirmable via DevTools).
- The `#movie-status` live region announces "Marked as watched."
- Reloading the page preserves the watched state (server-persisted).

- [ ] **Step 6: Manual browser verification — click to unmark**

Click the green "Watched" button.

Verify:
- Button reverts to `Mark as Watched` with the eye icon and muted gray color.
- `aria-pressed` flips to `"false"`.
- Live region announces "Removed from watched."

- [ ] **Step 7: Manual browser verification — mobile viewport (< 640px)**

In DevTools, resize the viewport to 375px wide (iPhone SE) or use the device emulator.

Verify:
- The middle button's label shortens: the span `Mark as ` disappears, leaving just `Watched` with its eye icon.
- All three buttons still fit within the bar without overflow.
- Watched-state label remains `Watched` (unchanged across breakpoints).

- [ ] **Step 8: Confirm no new inline scripts were introduced**

Run: `python3 -m pytest tests/web/test_static_script_boundaries.py -v`
Expected: both existing tests still pass — `test_movie_card_template_delegates_browser_behavior_to_static_assets` confirms the template still has zero inline `<script>` blocks.

- [ ] **Step 9: Commit**

```bash
git add static/css/output.css
git commit -m "build(css): regenerate Tailwind output for watched button redesign"
```

---

## Verification summary

When all five tasks complete, the following must be true:

| Check | How to verify |
|-------|---------------|
| `--color-watched` token exists in all 4 theme declarations | `pytest tests/web/test_watched_button_redesign.py -v` (Task 1 tests) |
| `.nav-btn-watched` styles present in `input.css` | Task 2 tests |
| Watched form relocated into sticky nav bar, correctly gated | Task 3 tests |
| JS no longer references Tailwind utility strings | Task 4 tests |
| Full test suite passes | `pytest tests/ -q` |
| No inline scripts in templates | `pytest tests/web/test_static_script_boundaries.py -v` |
| Visual: 3-button bar when logged in, 2-button bar when logged out | Browser check (Task 5) |
| Visual: mobile label shortens at < 640px | Browser check (Task 5) |
| Toggle still round-trips to `/watched/add` and `/watched/remove` | Browser check (Task 5) |

---

## Rollback

If the redesign needs to be reverted, each task was a single commit — `git revert` them in reverse order (Task 5 → Task 1). The routes and JSON API are untouched, so no data or session invalidation is needed.
