# Filter Drawer — Design Spec

## Context

Currently, adjusting filters requires navigating away from the movie page to `/filters`, changing settings, and submitting — which breaks the discovery flow. Users who want to tweak filters while browsing movies must leave the page entirely.

This feature adds a slide-out filter drawer directly on movie detail pages, letting users adjust and apply filters without leaving the current movie. The drawer is triggered by an edge tab on the right side of the viewport and keeps the user in their browsing flow.

## Requirements

- Right-side slide-out drawer on movie detail pages (`movie_card.html`) only
- Vertical edge tab trigger ("FILTERS") attached to the right viewport edge, accent-colored
- All filters from `set_filters.html` included: IMDb score range, vote count range, year range, language, genres, exclude watched
- Filters organized into collapsible sections to manage drawer width
- Applying filters fetches a new movie immediately; drawer stays open for iteration
- Home page unchanged — keeps existing "set filters first" link to `/filters`

## Architecture

### Shared Filter Partial

Extract the filter form fields from `set_filters.html` into a new `templates/_filter_form.html` partial.

- **Partial contents:** All form fields (score inputs, vote inputs, year inputs, language select, genre checkboxes, exclude-watched checkbox). No page chrome, headers, hero sections, or submit buttons.
- **`set_filters.html`** — replaces its inline form body with `{% include '_filter_form.html' %}`, wraps with its own page layout and Apply/Reset buttons.
- **`movie_card.html`** — includes `_filter_form.html` inside the drawer `<aside>`, wraps with drawer-specific Apply/Reset buttons.
- The partial accepts a `context` variable ("page" or "drawer") for minor styling differences (e.g., compact spacing in drawer mode).

### No Backend Filter Changes

Both the full page and the drawer submit to the same `/filtered_movie` POST endpoint with identical field names. Existing `normalize_filters()`, `validate_filters()`, and `criteria_from_filters()` in `infra/filter_normalizer.py` are reused as-is.

## Drawer Component

### Edge Tab (Trigger)

- Fixed-position vertical tab on the right edge of the viewport, vertically centered
- Dimensions: ~28px wide × ~80px tall
- Accent background (`--color-accent`: `#b0654f` light / `#c67a5c` dark)
- White text "FILTERS" rotated vertically (`writing-mode: vertical-rl`)
- `border-radius: 6px 0 0 6px` (rounded on left side only)
- Subtle left box-shadow for depth
- On mobile (< 640px): repositions to bottom-right corner as a smaller circular FAB with filter icon
- Hidden when drawer is open (it's structurally part of the drawer, slides offscreen with it)

### Drawer Panel

- Width: 320px on desktop, 100vw on mobile (< 640px)
- Background: `--color-surface` with `--color-border` left border
- Slides in from right via CSS `transform: translateX(100%)` → `translateX(0)`
- Transition: `transform 200ms ease` (uses `--duration-normal` token)
- Z-index above movie content but below any modals

### Drawer Header

- "Filters" title (bold) on the left
- Close button (×) on the right — circular, `--color-surface-alt` background

### Collapsible Sections

Three sections, each with a clickable header that toggles expand/collapse:

1. **Ratings & Votes** (default: expanded)
   - IMDb Score: min/max number inputs
   - Vote Count: min/max number inputs

2. **Year & Language** (default: collapsed)
   - Release Year: min/max number inputs
   - Language: select dropdown

3. **Genres** (default: collapsed)
   - Genre checkboxes in flex-wrap layout (same chip style as `set_filters.html`)
   - "Select All" toggle checkbox

4. **Exclude Watched** (default: collapsed, only shown when `current_user_id` is set)
   - Single checkbox with hidden input fallback (same pattern as `set_filters.html`)

Expand/collapse uses `max-height` CSS transition. Section headers show ▼ (expanded) / ▶ (collapsed) indicators.

Section open/closed states are persisted in `sessionStorage` so they survive movie-to-movie navigation.

### Drawer Footer (Actions)

- **Apply Filters** button — accent background, full-width primary action
- **Reset** button — secondary style, restores defaults

### Backdrop Overlay

- Semi-transparent dark overlay (`rgba(0,0,0,0.5)`) covering the movie content when drawer is open
- Clicking the backdrop closes the drawer
- Fade transition matches drawer timing

## JavaScript Behavior

### New file: `static/js/filter-drawer.js`

**Open/Close:**
- Toggle `drawer-open` class on the drawer container element
- Edge tab click → open
- Close button click, backdrop click, Escape key → close
- Focus trap: Tab/Shift+Tab cycles within the drawer when open

**AJAX Form Submission:**
- Intercept drawer form `submit` event via `addEventListener`
- Build `FormData` from the form (includes CSRF token from hidden input)
- `fetch('/filtered_movie', { method: 'POST', body: formData })` with header `X-Requested-With: FilterDrawer`
- Show loading spinner on Apply button during request
- On success (server returns JSON with redirect URL): set `sessionStorage` flag `filterDrawerOpen=true`, then `window.location.href = redirectUrl`
- On validation error (400 JSON response): display inline error messages next to fields within the drawer
- On network/server error: show a toast notification, keep drawer open

**Drawer Persistence Across Navigation:**
- Before navigating to new movie: set `sessionStorage.setItem('filterDrawerOpen', 'true')`
- On page load: if `sessionStorage.getItem('filterDrawerOpen') === 'true'`, auto-open the drawer
- Clear the flag when user explicitly closes the drawer

**Collapsible Sections:**
- Click section header → toggle `collapsed` class on section body
- Store section states in `sessionStorage` as JSON: `filterSections: { "ratings": true, "year": false, "genres": false }`
- Restore states on page load

## Backend Changes

### `routes.py` — `/filtered_movie` endpoint

Small modification to support AJAX requests from the drawer:

- Check for `X-Requested-With: FilterDrawer` header (or `request.is_json` / `Accept: application/json`)
- **If AJAX request:**
  - On success: return JSON `{ "redirect": "/movie/tt0111161" }` instead of HTTP redirect
  - On validation error: return JSON `{ "errors": { "imdb_score_min": "..." } }` with 400 status
- **If normal form POST (existing behavior):** unchanged — redirect or re-render `set_filters.html`

This is the only backend change. All filter parsing, normalization, and validation logic is reused.

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `templates/_filter_form.html` | Create | Shared filter form partial |
| `templates/set_filters.html` | Modify | Replace inline form with `{% include %}` |
| `templates/movie_card.html` | Modify | Add drawer aside, edge tab, backdrop, include partial |
| `static/css/input.css` | Modify | Drawer component styles, edge tab, collapsible sections, backdrop |
| `static/js/filter-drawer.js` | Create | Drawer mechanics, AJAX submit, collapsible sections, keyboard |
| `routes.py` | Modify | Return JSON for AJAX requests to `/filtered_movie` |

**Unchanged:** `filter_contracts.py`, `movies/filter_parser.py`, `infra/filter_normalizer.py`, `movie_service.py`, `templates/navbar_modern.html`, `templates/home.html`

## Verification

1. **Manual testing:**
   - Start dev server (`python3 app.py`)
   - Navigate to a movie page — verify edge tab is visible on right edge
   - Click edge tab — drawer slides open with all filter sections
   - Expand/collapse sections — verify animation and state persistence
   - Apply filters — verify new movie loads and drawer stays open
   - Reset filters — verify defaults are restored
   - Close drawer (X, backdrop, Escape) — verify edge tab reappears
   - Navigate prev/next — verify drawer state persists if it was open

2. **Full filters page regression:**
   - Navigate to `/filters` — verify the page still works identically
   - Submit filters from the full page — verify same behavior as before

3. **Mobile testing:**
   - Resize to < 640px — verify drawer goes full-width
   - Verify edge tab becomes bottom-right FAB

4. **Run existing tests:**
   - `python3 -m pytest tests/ -v` — all existing tests should pass (no breaking changes to filter logic)
   - Specifically: `test_filter_normalizer.py`, `test_filter_backend_extract.py`, `test_routes_extended.py`

5. **Rebuild CSS:**
   - `npm run build-css` — verify Tailwind output includes new drawer styles
