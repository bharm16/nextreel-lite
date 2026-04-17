# Remove the Set Filters Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the standalone `/filters` page now that the filter drawer on movie detail pages is a complete replacement. Preserve "Save as default" by moving it into the drawer footer.

**Architecture:** The filter drawer (in `templates/movie.html`) already uses the same `_filter_form.html` partial as the page. The page has three tendrils — navbar/home links, the "Save as default" button, and a no-match redirect destination — each of which is rewritten or removed. The drawer becomes the sole filter UI.

**Tech Stack:** Quart (async Flask) templates, vanilla JS drawer, pytest-asyncio route tests, Playwright e2e tests.

**Spec:** `docs/superpowers/specs/2026-04-16-remove-set-filters-page-design.md`

---

## File Structure Overview

Each task produces a self-contained, commit-sized change. Ordering preserves backward compatibility until the final removal step (Task 6) so intermediate commits never break the app.

| File | Role in this plan |
|---|---|
| `templates/movie.html` | Drawer footer gains "Save as default" button (Task 1) |
| `static/js/filter-drawer.js` | Submit handler respects `event.submitter.formAction` (Task 1) |
| `nextreel/web/routes/navigation.py` | `filtered_movie_endpoint` no-match + validation branches updated (Tasks 2, 3); `set_filters` deleted (Task 6) |
| `tests/web/test_routes_navigation.py` | Tests updated/added for the two behavior changes (Tasks 2, 3) and set_filters removal (Task 6) |
| `tests/integration/test_workflows_e2e.py` | `/filters` flows rewritten for drawer or deleted (Task 4) |
| `templates/navbar_modern.html` | Both "Filters" links removed (Task 5) |
| `templates/home.html` | "or set filters first" link removed (Task 5) |
| `nextreel/web/routes/shared.py` | `_render_filters_page` helper deleted (Task 6) |
| `nextreel/web/routes/__init__.py` | `set_filters` export removed (Task 6) |
| `templates/set_filters.html` | Deleted (Task 6) |
| `tests/web/test_app.py` | `test_set_filters_route` deleted; harmless-endpoint comment updated (Task 6) |
| `tests/structure/test_route_module_boundaries.py` | `"set_filters"` removed from expected endpoint list (Task 6) |

---

### Task 1: Add "Save as default" button to drawer and fix submit handler

**Files:**
- Modify: `templates/movie.html:83-89`
- Modify: `static/js/filter-drawer.js:293-312`
- Test: `tests/web/test_routes_navigation.py` (new test class `TestDrawerSaveAsDefaultButton`)

- [ ] **Step 1: Write a failing test for the drawer "Save as default" button visibility**

Add to `tests/web/test_routes_navigation.py`, below the existing `TestFiltersRoute` class:

```python
class TestDrawerSaveAsDefaultButton:
    async def test_button_rendered_for_logged_in_user(self):
        app, _ = _make_app()
        state = _nav_state(user_id="user-123")
        async with app.app_context():
            client = app.test_client()
            with patch(
                "nextreel.web.routes.navigation._current_state",
                return_value=state,
            ), patch(
                "nextreel.web.routes.movies._current_state",
                return_value=state,
            ):
                response = await client.get("/movie/tt1234567")
                body = await response.get_data(as_text=True)
                assert response.status_code == 200
                assert 'formaction="/account/preferences/filters/save"' in body
                assert "Save as default" in body

    async def test_button_absent_for_anonymous_user(self):
        app, _ = _make_app()
        state = _nav_state(user_id=None)
        async with app.app_context():
            client = app.test_client()
            with patch(
                "nextreel.web.routes.navigation._current_state",
                return_value=state,
            ), patch(
                "nextreel.web.routes.movies._current_state",
                return_value=state,
            ):
                response = await client.get("/movie/tt1234567")
                body = await response.get_data(as_text=True)
                assert response.status_code == 200
                assert "Save as default" not in body
                assert "/account/preferences/filters/save" not in body
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/web/test_routes_navigation.py::TestDrawerSaveAsDefaultButton -v`
Expected: FAIL — "Save as default" not in body (the drawer has no such button yet).

- [ ] **Step 3: Add the "Save as default" button to the drawer footer**

Edit `templates/movie.html`. Replace the `<div class="filter-drawer-footer">` block (currently lines 83-89) with:

```jinja
  <div class="filter-drawer-footer">
    <button type="submit" form="drawerFilterForm" id="drawerApplyBtn" class="filter-apply-btn" aria-busy="false">
      <svg class="h-4 w-4 hidden animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10" opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
      <span>Apply Filters</span>
    </button>
    <button type="reset" form="drawerFilterForm" id="drawerResetBtn" class="filter-reset-btn">Reset</button>
    {% if current_user_id %}
    <button type="submit"
            form="drawerFilterForm"
            formaction="{{ url_for('main.account_filters_save') }}"
            class="filter-reset-btn">
      Save as default
    </button>
    {% endif %}
  </div>
```

The new button reuses `.filter-reset-btn` styling (secondary treatment) to match the visual hierarchy where Apply is primary.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/web/test_routes_navigation.py::TestDrawerSaveAsDefaultButton -v`
Expected: PASS for both tests.

- [ ] **Step 5: Update the drawer JS submit handler to respect `formaction`**

Edit `static/js/filter-drawer.js`. Find the block starting at line 293:

```js
  form.addEventListener("submit", function (e) {
    e.preventDefault();
```

Replace with:

```js
  form.addEventListener("submit", function (e) {
    // If a submitter button overrides formaction (e.g. "Save as default"),
    // let the browser handle the submission natively instead of hijacking it.
    if (e.submitter && e.submitter.formAction) {
      try {
        var targetPath = new URL(e.submitter.formAction).pathname;
        if (targetPath !== "/filtered_movie") {
          return;
        }
      } catch (err) {
        // Malformed URL — fall through to AJAX
      }
    }
    e.preventDefault();
```

- [ ] **Step 6: Verify the full test suite still passes**

Run: `python3 -m pytest tests/web/test_routes_navigation.py -v`
Expected: PASS.

- [ ] **Step 7: Manual smoke test (user-run, optional)**

Start the app (`python3 app.py`), log in, navigate to any movie detail page, open the drawer, click "Save as default". Expected: browser navigates to `/account` (native form POST → redirect to `account_view`). Then re-open the drawer and click "Apply Filters". Expected: AJAX request to `/filtered_movie` (network tab shows XHR), page navigates to a new movie without a full reload.

- [ ] **Step 8: Commit**

```bash
git add templates/movie.html static/js/filter-drawer.js tests/web/test_routes_navigation.py
git commit -m "feat: move Save as default button into filter drawer footer"
```

---

### Task 2: Change `/filtered_movie` no-match redirect to the current movie

**Files:**
- Modify: `nextreel/web/routes/navigation.py:203-205`
- Test: `tests/web/test_routes_navigation.py` (new tests in `TestFilteredMovieRoute`)

- [ ] **Step 1: Write failing tests for the no-match redirect behavior**

Add to `tests/web/test_routes_navigation.py`, inside class `TestFilteredMovieRoute` (after the existing `test_json_no_matches_returns_error`):

```python
    async def test_html_no_matches_redirects_to_current_movie(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=None)
        manager.get_current_movie_tconst = MagicMock(return_value="tt7654321")
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={"year_min": "2000"},
            )
        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/tt7654321")

    async def test_html_no_matches_redirects_to_home_when_no_current_movie(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=None)
        manager.get_current_movie_tconst = MagicMock(return_value=None)
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={"year_min": "2000"},
            )
        assert response.status_code == 303
        # url_for('main.home') is '/'
        assert response.headers["Location"].endswith("/")
        assert not response.headers["Location"].endswith("/filters")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/web/test_routes_navigation.py::TestFilteredMovieRoute::test_html_no_matches_redirects_to_current_movie tests/web/test_routes_navigation.py::TestFilteredMovieRoute::test_html_no_matches_redirects_to_home_when_no_current_movie -v`
Expected: FAIL — current code redirects to `/filters` instead.

- [ ] **Step 3: Update `filtered_movie_endpoint` to redirect to the current movie**

Edit `nextreel/web/routes/navigation.py`. Find the block at lines 202-205:

```python
    if wants_json:
        return _no_matches_response()
    await flash("No movies matched your filters. Try broadening your criteria.", "warning")
    return redirect(url_for("main.set_filters"))
```

Replace with:

```python
    if wants_json:
        return _no_matches_response()
    await flash("No movies matched your filters. Try broadening your criteria.", "warning")
    tconst = movie_manager.get_current_movie_tconst(state)
    if tconst:
        return redirect(url_for("main.movie_detail", tconst=tconst))
    return redirect(url_for("main.home"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/web/test_routes_navigation.py::TestFilteredMovieRoute -v`
Expected: PASS (all tests in the class, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/navigation.py tests/web/test_routes_navigation.py
git commit -m "refactor(navigation): redirect to current movie on no-match instead of /filters"
```

---

### Task 3: Drop the non-JSON validation branch in `/filtered_movie`

**Files:**
- Modify: `nextreel/web/routes/navigation.py:142-170`
- Modify: `tests/web/test_routes_navigation.py` (rewrite 3 existing tests)

- [ ] **Step 1: Update the existing HTML-response validation tests to assert JSON**

Edit `tests/web/test_routes_navigation.py`. Replace three existing tests:

**(a)** Replace `test_invalid_filters_render_form_with_400_without_calling_manager` (starts at line 148) with:

```python
    async def test_invalid_filters_return_json_400_without_calling_manager(self):
        app, manager = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={
                    "year_min": "2025",
                    "year_max": "1990",
                },
            )
            data = await response.get_json()

        assert response.status_code == 400
        assert data["ok"] is False
        assert "year_min" in data["errors"] or "year_max" in data["errors"]
        manager.apply_filters.assert_not_awaited()
```

**(b)** Delete `test_invalid_filters_with_no_genres_show_all_genres_notice` entirely (it tested a template-only notice that no longer exists). The JSON validation-error path already returns the raw field errors, which is what the drawer consumes.

**(c)** Replace `test_invalid_filters_do_not_persist_exclude_watched_or_apply_filters` (starts around line 277) with:

```python
    async def test_invalid_filters_do_not_persist_exclude_watched_or_apply_filters(self):
        app, manager = _make_app()
        state = _nav_state(user_id="user-123")
        async with app.app_context():
            client = app.test_client()
            with (
                patch("nextreel.web.routes.navigation._current_state", return_value=state),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watched_default",
                    new_callable=AsyncMock,
                ) as set_exclude_watched_default,
            ):
                response = await client.post(
                    "/filtered_movie",
                    headers={"X-CSRFToken": "test-csrf-token"},
                    form={
                        "year_min": "2025",
                        "year_max": "1990",
                        "exclude_watched": "off",
                    },
                )
                data = await response.get_json()

        assert response.status_code == 400
        assert data["ok"] is False
        set_exclude_watched_default.assert_not_awaited()
        manager.apply_filters.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/web/test_routes_navigation.py::TestFilteredMovieRoute -v`
Expected: The three replaced/deleted tests FAIL because the current code returns HTML with `_render_filters_page`, not JSON. The existing `test_json_validation_errors_return_400` still passes.

- [ ] **Step 3: Drop the non-JSON validation branch in `filtered_movie_endpoint`**

Edit `nextreel/web/routes/navigation.py`. Find the block at lines 144-170:

```python
    if validation_errors:
        logger.info(
            "Rejected invalid filters for session_id: %s. Correlation ID: %s. Errors: %s",
            state.session_id,
            g.correlation_id,
            validation_errors,
        )
        elapsed_time = time.time() - start_time
        logger.info(
            "Completed filtering movies for session_id: %s in %.2f seconds. Correlation ID: %s",
            state.session_id,
            elapsed_time,
            g.correlation_id,
        )
        if wants_json:
            return jsonify({"ok": False, "errors": validation_errors}), 400
        return await _render_filters_page(
            filters,
            validation_errors=validation_errors,
            form_notice="Fix the highlighted filters and try again.",
            genres_notice=(
                "No genres selected. Nextreel will use all genres."
                if not filters.get("genres_selected")
                else None
            ),
            status_code=400,
        )
```

Replace with:

```python
    if validation_errors:
        logger.info(
            "Rejected invalid filters for session_id: %s. Correlation ID: %s. Errors: %s",
            state.session_id,
            g.correlation_id,
            validation_errors,
        )
        elapsed_time = time.time() - start_time
        logger.info(
            "Completed filtering movies for session_id: %s in %.2f seconds. Correlation ID: %s",
            state.session_id,
            elapsed_time,
            g.correlation_id,
        )
        return jsonify({"ok": False, "errors": validation_errors}), 400
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/web/test_routes_navigation.py::TestFilteredMovieRoute -v`
Expected: PASS for all tests in the class.

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/navigation.py tests/web/test_routes_navigation.py
git commit -m "refactor(navigation): always return JSON 400 for /filtered_movie validation errors"
```

---

### Task 4: Update e2e tests to use the drawer instead of `/filters`

**Files:**
- Modify: `tests/integration/test_workflows_e2e.py` (references at lines 95, 226, 230, 253, 293, 316, 493, 580, 582)

- [ ] **Step 1: Read the current e2e references**

Run: `grep -n "/filters" tests/integration/test_workflows_e2e.py`

Read each matching test fully to understand its intent. Classify each into:
- **Rewrite:** happy-path filter apply + no-match flow (preserve coverage by switching to drawer).
- **Delete:** flows that were only testing the standalone page UX (e.g., page loads, genre select-all, mobile menu).

Typical classification (confirm by reading):
- `page.goto(f"{BASE_URL}/filters")` followed by form submit → **rewrite** (navigate to a movie page, open drawer, submit drawer form).
- `page.locator("a[href='/filters']").first.click()` to open the page → **delete** the assertion; if the test only checked the link, delete the entire test body.

- [ ] **Step 2: Rewrite the happy-path filter flow**

For the first `/filters`-using test that exercises a filter submit and expects a movie result, change:

```python
page.goto(f"{BASE_URL}/filters")
# ... fill form fields ...
page.locator("button[type='submit']").click()
```

To:

```python
# Land on a movie detail page so the drawer is available
page.goto(f"{BASE_URL}/")
page.locator("form[action='/next_movie'] button[type='submit']").first.click()
page.wait_for_url(f"{BASE_URL}/movie/*")

# Open the drawer
page.locator("#filterDrawerTab").click()
page.wait_for_selector("#filterDrawer.open")

# Fill form fields (IDs are shared with the old page because both use _filter_form.html)
# ... fill form fields ...

# Submit via drawer Apply button (AJAX)
page.locator("#drawerApplyBtn").click()
page.wait_for_url(f"{BASE_URL}/movie/*")
```

- [ ] **Step 3: Rewrite the no-match flow**

For the test that submits filters expecting zero matches, after the drawer-based submit, expect the page to stay on a movie detail URL (the new no-match redirect) rather than navigating to `/filters`:

```python
# Before: assert "/filters" in page.url
# After:
assert "/movie/" in page.url
# Verify the no-match flash message appears
assert page.locator("text=No movies matched your filters").is_visible()
```

- [ ] **Step 4: Delete the remaining `/filters` flows**

For each remaining test that only asserts the `/filters` link/page exists, delete the test function. Specifically, tests whose sole purpose is:
- Asserting the navbar "Filters" link is clickable
- Visiting `/filters` to verify the standalone page renders
- Asserting `/filters` is in `page.url` after a link click

These behaviors do not exist after this refactor. Their coverage (drawer renders on movie pages) is already covered by `TestDrawerSaveAsDefaultButton` in `test_routes_navigation.py`.

- [ ] **Step 5: Run the e2e tests to verify the remaining ones pass**

Run: `python3 -m pytest tests/integration/test_workflows_e2e.py -v`

Expected: PASS for rewritten tests, others still pass. If the e2e suite requires a running server, follow the project's existing e2e instructions; if skipped by default, a quick `grep -n "/filters" tests/integration/test_workflows_e2e.py` to confirm there are no `/filters` references left is sufficient validation at this stage.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_workflows_e2e.py
git commit -m "test(e2e): migrate /filters flows to filter drawer"
```

---

### Task 5: Remove navbar "Filters" links and home "or set filters first" link

**Files:**
- Modify: `templates/navbar_modern.html:14`, `templates/navbar_modern.html:53`
- Modify: `templates/home.html:204`

- [ ] **Step 1: Remove the desktop navbar "Filters" link**

Edit `templates/navbar_modern.html`. Delete the entire line at line 14:

```jinja
    <a href="{{ url_for('main.set_filters') }}" class="navbar-link">Filters</a>
```

- [ ] **Step 2: Remove the mobile menu "Filters" link**

In the same file, delete the line at line 53:

```jinja
    <a href="{{ url_for('main.set_filters') }}">Filters</a>
```

- [ ] **Step 3: Remove the home "or set filters first" link**

Edit `templates/home.html`. Delete the line at line 204:

```jinja
      <a href="{{ url_for('main.set_filters') }}" class="home-secondary">or set filters first</a>
```

- [ ] **Step 4: Verify home and movie pages still render**

Run: `python3 -m pytest tests/web/ -v`
Expected: PASS. Home and movie-detail tests still hit 200. No `url_for('main.set_filters')` BuildError because the `set_filters` route still exists at this point.

- [ ] **Step 5: Commit**

```bash
git add templates/navbar_modern.html templates/home.html
git commit -m "refactor(templates): remove navbar and home links to /filters"
```

---

### Task 6: Remove the `/filters` route, template, helper, and exports

**Files:**
- Modify: `nextreel/web/routes/navigation.py` (delete `set_filters` function + unused imports)
- Modify: `nextreel/web/routes/shared.py` (delete `_render_filters_page` + `__all__` entry)
- Modify: `nextreel/web/routes/__init__.py` (drop `set_filters` from imports and `__all__`)
- Delete: `templates/set_filters.html`
- Modify: `tests/structure/test_route_module_boundaries.py:23` (remove `"set_filters"` from expected list)
- Modify: `tests/web/test_app.py` (delete `test_set_filters_route`, fix comment at line 19)
- Modify: `tests/web/test_routes_navigation.py` (delete `TestFiltersRoute` class; add 404 test)

- [ ] **Step 1: Update the structural test for the route module boundary**

Edit `tests/structure/test_route_module_boundaries.py:20-25`. Change:

```python
        "nextreel.web.routes.navigation": [
            "next_movie",
            "previous_movie",
            "set_filters",
            "filtered_movie_endpoint",
        ],
```

To:

```python
        "nextreel.web.routes.navigation": [
            "next_movie",
            "previous_movie",
            "filtered_movie_endpoint",
        ],
```

- [ ] **Step 2: Replace `test_set_filters_route` with a 404 test in `test_app.py`**

Edit `tests/web/test_app.py`. Replace the function at lines 42-49:

```python
async def test_set_filters_route():
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        MockManager.return_value.start = AsyncMock()
        app = _make_test_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/filters")
            assert response.status_code == 200
```

With:

```python
async def test_filters_route_returns_404():
    """The standalone /filters page has been removed; the drawer replaces it."""
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        MockManager.return_value.start = AsyncMock()
        app = _make_test_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/filters")
            assert response.status_code == 404
```

Also update the misleading comment in `_get_csrf_token`. Change lines 17-26:

```python
async def _get_csrf_token(client):
    """Issue a GET to establish a session and extract the CSRF token."""
    # Use /filters as a harmless GET endpoint that renders a template.
    # After the GET the session will contain our CSRF token.
    # We can't easily read the session from outside, so we inject the
    # token via the cookie-backed session before POST.
    #
    # Simpler approach: just set the session token directly via the
    # test request context.  The CSRF machinery stores it at '_csrf_token'.
    pass
```

To:

```python
async def _get_csrf_token(client):
    """Issue a GET to establish a session and extract the CSRF token."""
    # Use / (home) as a harmless GET endpoint that renders a template.
    # After the GET the session will contain our CSRF token.
    # We can't easily read the session from outside, so we inject the
    # token via the cookie-backed session before POST.
    #
    # Simpler approach: just set the session token directly via the
    # test request context. The CSRF machinery stores it at '_csrf_token'.
    pass
```

- [ ] **Step 3: Replace the `TestFiltersRoute` class in `test_routes_navigation.py` with a 404 test**

Edit `tests/web/test_routes_navigation.py`. Replace the class at lines 447-453:

```python
class TestFiltersRoute:
    async def test_get_returns_200(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/filters")
            assert response.status_code == 200
```

With:

```python
class TestFiltersRoute:
    async def test_get_returns_404(self):
        """The /filters page was removed in favor of the drawer."""
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/filters")
            assert response.status_code == 404
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python3 -m pytest tests/web/test_app.py::test_filters_route_returns_404 tests/web/test_routes_navigation.py::TestFiltersRoute tests/structure/test_route_module_boundaries.py -v`
Expected: FAIL — the `/filters` route still returns 200, and the structural test fails because `set_filters` is still in the module.

- [ ] **Step 5: Delete the `set_filters` route and its unused imports**

Edit `nextreel/web/routes/navigation.py`. Delete the entire `set_filters` function (lines 86-121) along with the leading blank line. Also update the module-level imports.

Replace the imports block at lines 10-32:

```python
from infra.metrics import user_actions_total
from infra.filter_normalizer import (
    default_filter_state,
    filters_from_criteria,
    normalize_filters,
    validate_filters,
)
from infra.route_helpers import csrf_required, rate_limited, with_timeout
from nextreel.domain.filter_contracts import FilterState
from nextreel.web.routes.shared import (
    _REQUEST_TIMEOUT,
    _current_state,
    _current_user_id,
    _legacy_session,
    _no_matches_response,
    _redirect_for_navigation_outcome,
    _render_filters_page,
    _services,
    _wants_json_response,
    bp,
    logger,
)
from session import user_preferences
from session.user_preferences import set_exclude_watched_default
```

With:

```python
from infra.metrics import user_actions_total
from infra.filter_normalizer import normalize_filters, validate_filters
from infra.route_helpers import csrf_required, rate_limited, with_timeout
from nextreel.domain.filter_contracts import FilterState
from nextreel.web.routes.shared import (
    _REQUEST_TIMEOUT,
    _current_state,
    _legacy_session,
    _no_matches_response,
    _redirect_for_navigation_outcome,
    _services,
    _wants_json_response,
    bp,
    logger,
)
from session.user_preferences import set_exclude_watched_default
```

Update the `__all__` at the bottom (lines 208-213). Replace:

```python
__all__ = [
    "filtered_movie_endpoint",
    "next_movie",
    "previous_movie",
    "set_filters",
]
```

With:

```python
__all__ = [
    "filtered_movie_endpoint",
    "next_movie",
    "previous_movie",
]
```

- [ ] **Step 6: Delete `_render_filters_page` from `shared.py`**

Edit `nextreel/web/routes/shared.py`. Delete the `_render_filters_page` function (lines 255-271) and remove `"_render_filters_page"` from the `__all__` list at line 291.

- [ ] **Step 7: Drop `set_filters` from `routes/__init__.py`**

Edit `nextreel/web/routes/__init__.py`. In the import block at lines 30-35, change:

```python
from nextreel.web.routes.navigation import (
    filtered_movie_endpoint,
    next_movie,
    previous_movie,
    set_filters,
)
```

To:

```python
from nextreel.web.routes.navigation import (
    filtered_movie_endpoint,
    next_movie,
    previous_movie,
)
```

Remove `"set_filters",` from the `__all__` at line 79.

- [ ] **Step 8: Delete the `set_filters.html` template**

```bash
rm templates/set_filters.html
```

- [ ] **Step 9: Run the full web + structural test suite to verify everything passes**

Run: `python3 -m pytest tests/web/ tests/structure/ -v`
Expected: PASS for all tests, including the two new 404 assertions.

- [ ] **Step 10: Run the full test suite one more time as a final gate**

Run: `python3 -m pytest tests/ -v`
Expected: PASS. (If e2e tests are excluded from the default run, ensure Task 4 has been committed first.)

- [ ] **Step 11: Commit**

```bash
git add \
    nextreel/web/routes/navigation.py \
    nextreel/web/routes/shared.py \
    nextreel/web/routes/__init__.py \
    templates/set_filters.html \
    tests/web/test_app.py \
    tests/web/test_routes_navigation.py \
    tests/structure/test_route_module_boundaries.py
git commit -m "refactor: remove /filters route, template, and related helpers"
```

---

## Final Verification

After all six tasks are committed:

- [ ] Run the full suite: `python3 -m pytest tests/ -v`
- [ ] Grep for any lingering references: `grep -rn "set_filters\|/filters" --include="*.py" --include="*.html" --include="*.js" .`
  Expected: only occurrences are in `docs/` (specs, old plans) and `CLAUDE.md`. If any source or test still mentions `main.set_filters` or the `/filters` URL, investigate before merging.
- [ ] Manual smoke test the happy path: start the app, log in, navigate to a movie, open the drawer, apply filters, click "Save as default", verify the account page confirms the save.
- [ ] Manual smoke test the no-match path: apply filters known to return zero results, verify the redirect lands on the current movie page with the flash message.
