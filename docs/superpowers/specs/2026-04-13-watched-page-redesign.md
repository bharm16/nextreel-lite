# Watched Page Redesign

**Date:** 2026-04-13
**Status:** Approved
**Branch:** `refactor/general-cleanup-v2`

## Summary

Redesign the `/watched` page from a plain thumbnail grid into a **browsable archive** — functional but beautiful. The page is a tool for finding and browsing your watched films, styled with the same editorial warmth as the login/register and movie detail pages.

### Core problems with the current design
1. Feels like a plain thumbnail dump — no personality, no editorial feel
2. Stats section is lifeless — just numbers with no visual warmth
3. Grid is too dense — tight gaps, too many columns, no breathing room

### Design identity
A **browsable archive**: the search/sort/filter toolbar is the hero functionality, posters serve the browsing experience, and stats provide warm context rather than dashboard data.

---

## Page Zones

The page uses Layout C (Breathing Header, Distinct Zones): generous header → hairline separator → toolbar band → poster grid. Each zone has its own visual space.

---

## Section 1: Page Header

- **Title:** "Watched" — Merriweather serif, 2.25rem, weight 300, letter-spacing -0.02em
- **Subtitle:** "*1,674 films and counting*" — Merriweather, 1rem, weight 300, italic, `var(--color-text-muted)`
- Count updates dynamically as films are added/removed
- Bottom margin: 2.5rem before toolbar zone
- No divider below — the toolbar zone's top border handles separation
- Scrolls with the page (not sticky)

---

## Section 2: Sticky Toolbar

### Structure
- Hairline top border (`1px solid var(--color-border)`) separating from header zone
- Padding top: 1.25rem; bottom margin: 2rem before grid

**Row 1: Search + Utilities**
- **Search input** (left): underline-style input matching login/register pattern, placeholder "Search films...", DM Sans 0.8rem. SVG magnifying glass icon (no emojis anywhere on the page)
- **Letterboxd icon** (right of search, before sort): small icon link, `var(--color-text-muted)` at rest, `var(--color-accent)` on hover
- **Sort dropdown** (right): native `<select>`, "Recent" default display, DM Sans 0.7rem, muted color with down caret

**Row 2: Filter Chips**
- **"All" chip** (default active): filled with `var(--color-accent)`, white text
- **Other chips:** outlined, 1px border `var(--color-border)`, muted text
- **Active chip:** swaps to accent fill (same as "All")
- **Typography:** DM Sans 0.65rem, uppercase, letter-spacing 0.05em
- **Categories mixed together:** decades (2020s, 2010s, 2000s...), rating tiers (8+, 6–8, <6), genres (Horror, Drama, Sci-Fi, Thriller...)
- Auto-generated from the user's actual watched data — no empty categories shown
- Multiple chips can be active simultaneously (e.g., "2020s" + "Horror")
- **Combination logic:** OR within a category (2020s OR 2010s), AND across categories (decade AND genre AND rating). Example: "2020s" + "2010s" + "Horror" = horror films from either decade.
- Gap between chips: 0.5rem, flex-wrap for overflow

### Sort Options (5)
1. Recently watched (default)
2. Alphabetical A–Z
3. Alphabetical Z–A
4. Year (newest first)
5. Rating (highest first)

### Sticky Behavior
- `position: sticky; top: 0`
- Transparent background at rest (no background when in natural position)
- On scroll: picks up `rgba(17, 17, 17, 0.4)` wash via JS scroll listener (dark mode)
- Light mode scroll wash: `rgba(245, 244, 240, 0.4)`
- Transition: `background-color 200ms ease`

---

## Section 3: Poster Grid

### Grid Layout
- `display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr))`
- Minimum 2 columns enforced
- Gap: 1.5rem on desktop, 1rem below 640px
- Resulting columns: ~6-7 wide desktop, ~4-5 tablet, ~2-3 phone

### Card at Rest
- Poster image only — no text, no metadata visible
- `aspect-ratio: 2/3`, `border-radius: 2px`, `overflow: hidden`
- Placeholder: solid `var(--color-surface)` background until image loads
- Images use `loading="lazy"` for native lazy loading
- Transition property set for `transform` (200ms ease)

### Card on Hover
- **Poster scales:** `transform: scale(1.03)`, origin center
- **Bottom bar slides up:**
  - Background: `rgba(17, 17, 17, 0.92)`
  - Top border: `1px solid var(--color-accent)`
  - Content: title (Merriweather, 0.85rem, weight 300) left-aligned, year (DM Sans, 0.65rem, uppercase, muted) right-aligned
  - Padding: 0.65rem 0.75rem
- **Remove icon:** small X/minus SVG icon, top-right corner of poster, appears on hover only
  - Muted color at rest, accent on hover
  - Separate from bottom bar
  - Also appears on `:focus-within` (keyboard accessibility)
- All hover elements transition at 200ms ease

### Click Behavior
- Clicking the poster navigates to `/movie/{tconst}`
- Clicking the remove icon removes the film and shows an undo toast
- Remove click does not propagate to the poster link (`event.stopPropagation()`)

---

## Section 4: Load More + End of List

### Load More Button
- Centered below grid, top margin 2.5rem
- Styled as a text link — DM Sans, 0.75rem, uppercase, letter-spacing 0.1em, muted color
- Text: "LOAD MORE"
- On hover: `var(--color-accent)`, no other decoration
- Loads next batch (60 films per batch) via fetch request to server, appends to existing grid DOM
- Server applies current filters and sort order to the paginated query — "Load more" fetches the next page with the same params
- Button disappears when all films are loaded

### End of List Mark
- Appears once all films are loaded
- Centered: short horizontal rule (2rem wide, 1px, `var(--color-border)`) with muted text below
- Text: "That's all 1,674" — DM Sans, 0.7rem, muted color, normal case
- Top margin 2.5rem from last grid row

### Undo Toast (Remove Action)
- Fixed to bottom-center of viewport
- Background: `var(--color-surface)`, 1px border `var(--color-border)`, 3px radius
- Text: "Removed from watched" + "Undo" link in `var(--color-accent)`
- Auto-dismisses after 5 seconds
- Typography: DM Sans, 0.8rem

---

## Section 5: Empty State

Rendered when the watched list has zero films. No toolbar, grid, or filter chips.

### Layout
- Centered vertically and horizontally, `min-height: 60vh`
- Max-width: 280px for CTAs

### Content
- **Title:** "Your film journey starts here" — Merriweather serif, 1.4rem, weight 300, `var(--color-text)`. Not italic.
- **Decorative rule:** 2rem wide, 1px, `var(--color-accent)` — centered below title
- **Primary CTA:** "Import from Letterboxd" — accent background, DM Sans 0.75rem uppercase, full-width (max 280px), small Letterboxd icon left of text
- **Secondary CTA:** "Pick a Movie" — text-link style, DM Sans 0.75rem, muted color, accent on hover. Links to home/discovery.
- CTAs stacked vertically, 1rem gap

---

## Section 6: Accessibility

### Keyboard Navigation
- Filter chips are focusable (`tabindex="0"`) with visible focus ring using `var(--color-accent)` outline
- Search input, sort dropdown, "Load more" button follow standard tab order
- Poster cards are `<a>` tags, natural tab order through the grid
- Remove button is keyboard-accessible, appears on `:focus-within`
- Undo toast "Undo" link is focusable and auto-focused on appearance

### Contrast (WCAG AA)
- Primary text: `#e8e6e3` on `#111111` = 13.8:1 (passes AAA)
- Muted text: `#888580` on `#111111` = 4.6:1 (passes AA)
- Accent: `#c67a5c` on `#111111` = 4.9:1 (passes AA, used on interactive elements only)

### Screen Reader Support
- Page: `aria-label="Watched films archive"`
- Filter chips: `role="group"` with `aria-label="Filter by"`, each chip is a toggle button with `aria-pressed`
- Sort: native `<select>` for full accessibility
- Remove button: `aria-label="Remove [film title] from watched"`
- Undo toast: `role="status"` with `aria-live="polite"`
- Grid: `role="list"`, each card `role="listitem"`
- Active filter count: "Showing 342 of 1,674 films" as `aria-live` region near search bar

### Light Mode
- All tokens swap via CSS custom properties — no separate stylesheets
- Toolbar scroll wash uses `rgba(245, 244, 240, 0.4)`
- All colors reference design tokens, adapt automatically

---

## Motion

Minimal and functional — no entrance animations, no staggered grid loading.

| Element | Trigger | Duration | Easing |
|---|---|---|---|
| Poster scale | hover/focus | 200ms | ease |
| Bottom bar slide | hover/focus | 200ms | ease |
| Remove icon appear | hover/focus-within | 200ms | ease |
| Toolbar background wash | scroll position | 200ms | ease |
| Filter chip toggle | click | 150ms | ease |

---

## Data Requirements

### Available per watched movie
- `tconst` (IMDb ID)
- `title`
- `year`
- `poster_url`
- `tmdb_rating`
- `watched_at`

### Required for filter chips (from projection data)
- `genres` — array of genre names, available when projection state is `ready`

### Filter chip generation logic
- **Decades:** group `year` values into decades, only show decades with ≥1 film
- **Rating tiers:** 8+ (great), 6–8 (solid), <6 (below average) — based on `tmdb_rating`
- **Genres:** union of all genres across the user's watched films (from enriched projections)

---

## Files to Modify

- `templates/watched_list.html` — complete rewrite of page structure
- `templates/_watched_card.html` — simplified poster-only card with hover overlay
- `static/css/input.css` — watched page component styles (lines ~1115-1453 replaced)
- `nextreel/web/routes/watched.py` — add genre aggregation, pagination support, filter/sort params
- `movies/watched_store.py` — add methods for filtered/sorted queries, genre aggregation
- `static/js/watched.js` (new) — scroll listener for toolbar wash, "Load more" fetch, undo toast, remove action, filter chip toggling. Search is client-side title filtering via JS (sufficient for personal archive size). Filters, sort, and pagination are server-side via query params.

---

## Out of Scope

- Virtualized grid rendering (deferred — "Load more" is sufficient for current scale)
- Full-text search (client-side title filtering via JS is sufficient for personal archive size)
- Custom sort orders or drag-to-reorder
- Batch selection / bulk remove
- Watch date editing
- Stats dashboard or viewing analytics
