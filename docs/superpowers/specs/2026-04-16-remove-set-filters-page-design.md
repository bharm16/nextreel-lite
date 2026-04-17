# Remove the Set Filters Page

**Date:** 2026-04-16
**Status:** Approved for planning
**Related specs:** `docs/superpowers/specs/2026-04-06-filter-drawer-design.md` (introduced the drawer), `docs/superpowers/specs/2026-04-14-account-settings-design.md` (introduced saved default filters)

## Background

The filter drawer (introduced in the 2026-04-06 spec) lives on movie detail pages and uses the shared `templates/_filter_form.html` partial. Functionally, the drawer is a complete replacement for the standalone `/filters` page (`templates/set_filters.html`).

Today both exist. The page is reachable via navbar ("Filters") links and a secondary link on the home page ("or set filters first"), plus it is the redirect destination for the no-match branch of `/filtered_movie` and the re-render destination for non-AJAX validation errors. It also hosts the only UI for "Save as default" (POSTs to `account_filters_save`).

Keeping both means two UI surfaces, two navigation paths, two code paths for validation errors, and two templates referencing the same partial. This spec removes the page.

## Goals

- Remove `/filters` as a user-facing route and the `set_filters.html` template.
- Preserve "Save as default" by moving the button into the drawer footer (logged-in only).
- Replace the no-match fallback redirect with one that keeps the user on the movie they already had.
- Simplify `/filtered_movie` by removing its non-AJAX error-render branch (the drawer is JS-only).
- Remove dead code: `_render_filters_page`, the home "or set filters first" link, navbar "Filters" entries.

## Non-goals

- Changes to the filter semantics, `_filter_form.html` partial, or `MovieManager.apply_filters`.
- Adding a new "Default filters" section to `/account`. The existing flow (drawer "Save as default" → redirect to `account_view`) is sufficient.
- Preserving the current set_filters-GET auto-seeding from saved defaults (see "Dropped behavior" below).
- Migration or deprecation period for `/filters`. The route returns 404 after deploy; this is acceptable because external links to `/filters` are minimal and the drawer is already the primary UI.

## Decisions

1. **Home entry point.** Remove the "or set filters first" link on `home.html`. Users pick a movie first, then open the drawer from the movie page.
2. **"Save as default" location.** Move into the drawer footer. Visible only when `current_user_id` is set. Uses the same `formaction` override pattern that `set_filters.html` uses today.
3. **No-match fallback.** On `POST /filtered_movie` with non-JSON `Accept` and zero matches, redirect to the current movie detail page (via `movie_manager.get_current_movie_tconst(state)`), falling back to home if no current tconst. The existing flash ("No movies matched your filters. Try broadening your criteria.") carries over.
4. **Validation errors, non-AJAX path.** Dropped. The drawer always submits with `Accept: application/json`, and no-JS users cannot open the drawer anyway (its visibility is JS-controlled). All validation errors now return JSON 400 unconditionally.
5. **Auto-seed from saved defaults.** Dropped. The current trigger (GET `/filters` with session at factory defaults) is niche and only fires on that route. If a broader "apply saved defaults on login" behavior is wanted later, it belongs in session initialization, not in a filter page.

## Architecture

### Removed

- **Route:** `set_filters` (GET `/filters`) in `nextreel/web/routes/navigation.py:86-121`.
- **Template:** `templates/set_filters.html`.
- **Helper:** `_render_filters_page` in `nextreel/web/routes/shared.py:255-271`.
- **Exports:** `set_filters` from `nextreel/web/routes/__init__.py`; `_render_filters_page` from `shared.py::__all__`.
- **Seeding logic:** the `get_default_filters` / `filters_from_criteria` branch currently at `navigation.py:95-104`, along with the supporting imports (`default_filter_state`, `filters_from_criteria`, `session.user_preferences`) that become unused in `navigation.py`.
- **Template link:** `templates/home.html:204` ("or set filters first").
- **Template links:** both "Filters" / "Set Filters" entries in `templates/navbar_modern.html` (desktop nav at line 14, mobile menu at line 53).

### Modified

- **`templates/movie.html`** — drawer footer gains a third button, logged-in only:

  ```jinja
  {% if current_user_id %}
  <button type="submit" form="drawerFilterForm"
          formaction="{{ url_for('main.account_filters_save') }}"
          class="filter-save-default-btn">
    Save as default
  </button>
  {% endif %}
  ```

  The button reuses the existing `.filter-reset-btn` styling (secondary/outlined treatment) — "Save as default" is a secondary action next to the primary Apply button, matching the visual hierarchy used on `set_filters.html` today where Apply is the primary CTA and "Save as default" is a secondary chip.

- **`static/js/filter-drawer.js`** — the submit handler at line 293 must respect `event.submitter.formAction`:

  ```js
  form.addEventListener("submit", function (e) {
    if (e.submitter && e.submitter.formAction &&
        new URL(e.submitter.formAction).pathname !== "/filtered_movie") {
      return; // native form submit for "Save as default"
    }
    e.preventDefault();
    // ...existing AJAX flow unchanged
  });
  ```

- **`nextreel/web/routes/navigation.py`**:
  - Delete `set_filters` function.
  - In `filtered_movie_endpoint`:
    - Delete the `validation_errors` non-JSON branch (the `else` path calling `_render_filters_page`). Return JSON 400 for all validation-error cases.
    - Replace the no-match non-JSON branch (currently flash + redirect to `main.set_filters`) with: `tconst = movie_manager.get_current_movie_tconst(state); redirect to main.movie_detail if tconst else main.home`. Flash message unchanged.
  - Remove imports no longer used: `_render_filters_page`, `default_filter_state`, `filters_from_criteria`, `user_preferences`.

- **`nextreel/web/routes/shared.py`** — delete `_render_filters_page` and remove it from `__all__`.

- **`nextreel/web/routes/__init__.py`** — drop `set_filters` from imports and `__all__`.

### Data flow

| Event | Today | After |
|---|---|---|
| User clicks "Filters" in navbar | GET `/filters` → renders page | N/A (link removed) |
| User clicks "or set filters first" on home | GET `/filters` → renders page | N/A (link removed) |
| User clicks drawer tab on movie page | Opens drawer, form pre-populated from session | unchanged |
| User clicks Apply in drawer | AJAX POST `/filtered_movie` | unchanged |
| Apply returns validation errors | 400 JSON, drawer renders errors | unchanged |
| Apply returns no matches | JSON no-matches response | unchanged |
| Apply returns success | JSON redirect → navigate | unchanged |
| User clicks "Save as default" in drawer | *(did not exist in drawer)* | Native POST to `account_filters_save` → redirect to `account_view` |
| Non-AJAX POST to `/filtered_movie` with validation errors | Re-render `set_filters.html` 400 | JSON 400 (consistent with AJAX path) |
| Non-AJAX POST to `/filtered_movie` with no matches | Flash + redirect to `/filters` | Flash + redirect to current movie detail (fallback: home) |

## Testing

### Delete

- `tests/web/test_app.py::test_set_filters_route`.
- `tests/web/test_app.py` line-19 comment ("Use /filters as a harmless GET endpoint"): replace the referenced endpoint with `/`.
- `tests/web/test_routes_navigation.py:452` — the GET `/filters` assertion.
- `tests/structure/test_route_module_boundaries.py:23` — remove `"set_filters"` from the expected endpoint list.

### Add

- `movie.html` renders "Save as default" for logged-in users and omits it for anonymous users.
- `POST /filtered_movie` with no matches:
  - non-JSON Accept + state with current tconst → 302 to `/movie/<tconst>`, flash preserved.
  - non-JSON Accept + empty state → 302 to `/`, flash preserved.
  - JSON Accept → `_no_matches_response()` (unchanged).
- `POST /filtered_movie` with validation errors + non-JSON Accept → 400 JSON (regression test for the dropped HTML branch).
- `GET /filters` → 404.

### Update (integration `tests/integration/test_workflows_e2e.py`)

- Happy-path filter apply and no-match flow: rewrite to open a movie page, click the `FILTERS` drawer tab, submit the drawer form.
- Remaining `/filters`-specific flows (~6): delete. Unit tests cover the drawer form behavior; e2e coverage is preserved for the two user journeys that matter most.

### Not tested

- Non-JS drawer submission — drawer visibility requires JS, so there is no no-JS path to defend.
- Auto-seed from saved defaults — behavior dropped.

## Dropped behavior

1. **Auto-seed from saved defaults on GET `/filters`.** Logged-in users whose session was at factory defaults had their saved default filters applied when they opened the page. This only fired on that one route. If a broader application point is desired (e.g., apply saved defaults at login), it belongs in session initialization in a separate spec.
2. **Visual browsing of filters as a standalone page.** Users who want to adjust filters must be on a movie detail page.
3. **Non-JS filter submission.** Already effectively broken (the drawer requires JS to open), formally removed here.

## Rollout

1. Land the template + JS changes (`movie.html`, `filter-drawer.js`, `home.html`, `navbar_modern.html`).
2. Land the route changes (`navigation.py`, `shared.py`, `__init__.py`, `set_filters.html` deletion).
3. Update tests in the same PR as their corresponding code changes.
4. Deploy.
5. Monitor 404s on `/filters` for one week. Expected: low volume from stale bookmarks.

## Risks

- **Stale external links to `/filters`** will 404. Acceptable: the drawer is already the primary UI, and affected users will land on home (Quart's default 404 or app-level handler).
- **"Save as default" discoverability** is somewhat reduced (buried in drawer footer vs. standalone page button). Mitigation: the account page already surfaces the saved-default state read-only; users who care can discover it there. If discoverability becomes a problem, a follow-up can add UI affordance on `/account`.
- **E2E rewrite** for the drawer-based filter flow is more fragile than the page-based one (needs a movie page in a known state first). Mitigation: reuse existing drawer-e2e fixtures if available.
