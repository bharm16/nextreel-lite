# Navbar Redesign

**Date:** 2026-04-17
**Status:** Approved
**Replaces:** Ad-hoc `templates/navbar_modern.html` (brand + single nav link + avatar)

## Goal

Replace the current thin navbar with a scroll-aware editorial action bar that solves the "feels empty, missing usefulness" problem without breaking the editorial design language established by the login, register, home, movie, watched, and account pages. The new bar is the primary surface for the app's two verbs (**Pick a Movie** and **Search**) and the wayfinder to **Watched** and **Account**. Filters remain in the existing drawer, untouched.

## Design Decisions Summary

| Dimension | Decision |
|-----------|----------|
| Purpose | Action bar (primary surface for Pick + Search), with editorial restraint in typography |
| Content inventory | Brand + tagline · ⌕ icon · Watched link · Pick pill · Avatar dropdown |
| Filters | **Not in the toolbar** — remain in the existing drawer tab |
| Tagline copy | "CINEMA DISCOVERY" (tracked caps, 9px desktop / 7px mobile) |
| Search behavior | Icon-only in bar; opens a Spotlight-style modal overlay with live results |
| Surface treatment | Scroll-aware — transparent at top, solid on scroll (40px threshold) |
| Density | Balanced (~80px desktop, ~58px mobile) |
| Motion | Editorial measured — 250ms ease-in-out for surface, 200ms for hover, 300ms for modal |
| Mobile layout | Persistent compact bar: brand + tagline · ⌕ · Pick pill · ≡ hamburger |
| Avatar dropdown | Account · Theme toggle (inline state) · Log Out |
| Logged-out state | Brand + tagline · ⌕ · Pick · "Log In" link (no Watched, no avatar) |
| Keyboard | Tab nav, ⌕ triggers Spotlight, `/` keybind opens Spotlight anywhere |
| Accessibility | 2px accent focus rings, aria-haspopup + aria-expanded on triggers, reduced-motion respected |
| Out of scope | home.html, filter drawer, floating hero arrows, streaming chips, notifications, search history |

## Layout

### Desktop (≥ 768px)

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ Nextreel          ⌕     WATCHED    [ ▶ PICK A MOVIE ]    (B)                  │
│ CINEMA DISCOVERY                                                              │
└───────────────────────────────────────────────────────────────────────────────┘
```

- Brand lockup is left-aligned.
- Everything after the brand receives `margin-left: auto` at the search icon, right-aligning the action cluster.
- Gap between right-cluster items: `16px`.
- Bar padding: `18px 26px` (vertical / horizontal).

### Mobile (< 768px)

```
┌────────────────────────────────────────────┐
│ Nextreel         ⌕  [PICK]  ≡              │
│ CINEMA DISCOVERY                           │
└────────────────────────────────────────────┘
```

- Pick pill text compressed to `"Pick"` (no `" a Movie"`).
- Hamburger opens the existing slide-down panel, contents updated.
- Bar padding: `14px 16px`, gap `8px`.

### Logged-out (desktop)

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ Nextreel          ⌕    [ ▶ PICK A MOVIE ]    LOG IN                           │
│ CINEMA DISCOVERY                                                              │
└───────────────────────────────────────────────────────────────────────────────┘
```

- No Watched, no avatar.
- `LOG IN` rendered as nav-link style (not a pill) — Pick remains the only primary action.

## Sections

### 1. Brand lockup

- Wordmark — Merriweather 700, 22px desktop / 17px mobile, `letter-spacing: -0.02em`, `line-height: 1`
- Tagline — DM Sans 500, 9px desktop / 7px mobile, UPPERCASE, `letter-spacing: 0.24em`, `margin-top: 4px`
- Colors:
  - Transparent state (over hero): wordmark `#fff`, tagline `rgba(255,255,255,0.5)`
  - Solid state (scrolled): wordmark `var(--color-text)`, tagline `var(--color-text-muted)`
- Entire lockup is an `<a href="/">` wrapped around both lines
- Focus-visible: 2px accent outline at 2px offset, radius 2px

### 2. Search icon (⌕) — Spotlight trigger

- 34×34 desktop / 30×30 mobile, 1px border, radius 3px
- Default: border `rgba(text, 0.16)`, color `rgba(text, 0.8)`
- Hover: border `rgba(text, 0.3)`, bg `rgba(text, 0.04)`, color full
- Active (modal open): accent border + 2px accent box-shadow ring
- Focus-visible: 2px accent outline at 2px offset
- Transparent vs solid state: uses the same relative rgba values against the current text color

### 3. Watched link

- Style per Typography table in §7
- Default: `rgba(text, 0.7)`
- Hover: color → full
- Current page indicator: color full + 1px accent underline at 2px below baseline
- Focus-visible: 2px accent outline at 2px offset, radius 2px

### 4. Pick a Movie pill

- Background `var(--color-accent)`, color `#fff`, radius 3px
- Padding: `10px 18px` desktop / `7px 12px` mobile
- Box-shadow: `0 2px 10px rgba(198,122,92,0.3)`
- Hover: bg `var(--color-accent-hover)` = `#b56a4d`, shadow deepens to `0 2px 14px rgba(198,122,92,0.5)`
- Active: `transform: scale(0.98)`
- Focus-visible: 2px accent ring at 2px offset
- Loading: existing spinner icon, `aria-busy="true"`, disabled pointer
- Leading `▶` icon (14px) on desktop, icon-less "Pick" on mobile to save space
- Posts to `/next_movie` via existing CSRF-guarded form (unchanged backend)

### 5. Avatar button + dropdown

**Avatar button**
- 32×32 round, 1px border `rgba(text, 0.12)`
- Open state: 2px accent ring via box-shadow, border `rgba(accent, 0.5)`
- `aria-haspopup="menu"`, `aria-expanded="true|false"`, `aria-controls="avatarMenu"`, `aria-label="Account menu"`

**Dropdown**
- Position: `absolute`, `top: 100%`, `right: 0`, `margin-top: 8px`
- Surface: `var(--color-surface)`, 1px `var(--color-border)`, radius 4px, shadow `0 12px 36px rgba(0,0,0,0.5)`
- Padding: `6px` outer, `8px 10px` per item
- Min-width: `180px`
- Opens with 300ms fade + 98%→100% scale
- Items (in order):
  1. **Account** (link to `/account`)
  2. 1px divider (`rgba(text, 0.06)`)
  3. **Theme** — button; right-aligned italic serif state indicator: `Dark ●` or `Light ○` (10px, `var(--color-text-muted)`)
  4. 1px divider
  5. **Log Out** — form-submit button, text color `rgba(text, 0.5)` (subtle destructive signal)
- Item style: DM Sans 600, 10.5px, UPPERCASE, `letter-spacing: 0.14em`, color `rgba(text, 0.7)`
- Item hover: bg `rgba(accent, 0.08)`, color `#f0b69a` (accent-light)
- Keyboard: ↑↓ arrows navigate items, Enter activates, Esc closes and returns focus to avatar button
- Focus trap while open; outside click closes

### 6. Spotlight search modal (new component)

**Triggers**
- ⌕ icon click
- `/` keypress anywhere on the page (unless a text input is focused)
- Escape closes

**Markup**
- Template: `templates/_search_spotlight.html`
- Included once at the end of `navbar_modern.html`

**Visual**
- Backdrop: full viewport, `position: fixed`, `inset: 0`, background `rgba(0,0,0,0.55)`, `backdrop-filter: blur(6px)`, z-index 100
- Container: centered horizontally, `margin-top: 80px`, `max-width: 560px`, width `calc(100% - 32px)` (so mobile has 16px gutters), `var(--color-surface)`, 1px border, radius 6px, shadow `0 20px 60px rgba(0,0,0,0.7)`, padding 16px
- Input: Merriweather italic 18px, placeholder `"Search films, actors…"`, no visible border, bottom 1px `rgba(text, 0.08)` separator between input and results
- Results list: flex column, `gap: 0`, each result row:
  - `40×60` gradient-placeholder thumbnail (posters not available from `movie_candidates`)
  - Title — DM Sans 500, 13px, `var(--color-text)`
  - Meta — Merriweather italic, 11px, `var(--color-text-muted)`, right-aligned (`year · ★ rating`)
  - Row padding: `8px 10px`, hover bg `rgba(accent, 0.08)`, active bg accent tint
  - Full row is a link to `/movie/<tconst>`

**Behavior**
- Debounce: 150ms after last keystroke → `fetch('/api/search?q=...')`
- Up to 10 results shown
- Keyboard: ↑↓ navigate highlighted row (CSS `.is-active`), Enter opens, Esc closes
- Empty state (no query): italic serif `"Start typing to search…"` in result area
- No results: italic serif `"No films found for \"xyz\"."`
- Error state: italic serif `"Couldn't reach the catalog. Try again."`
- Opens with 300ms fade + 98%→100% scale on the container; backdrop fades independently

**Accessibility**
- `role="dialog"`, `aria-modal="true"`, `aria-label="Search"`
- Focus traps within modal
- Return focus to ⌕ icon button on close
- Results list uses `role="listbox"`; rows use `role="option"`; active row gets `aria-selected="true"`

### 7. Typography reference

| Element | Font | Size (desk / mobile) | Weight | Case / Tracking |
|---|---|---|---|---|
| Brand wordmark | Merriweather | 22 / 17px | 700 | `-0.02em` |
| Tagline | DM Sans | 9 / 7px | 500 | UPPERCASE, `0.24em` |
| Nav link | DM Sans | 11.5 / 10.5px | 600 | UPPERCASE, `0.14em` |
| Pick pill | DM Sans | 11.5 / 9.5px | 700 | UPPERCASE, `0.12em` |
| Dropdown item | DM Sans | 10.5px | 600 | UPPERCASE, `0.14em` |
| Theme state (dropdown) | Merriweather italic | 10px | 400 | — |
| Spotlight input | Merriweather italic | 18px | 400 | — |
| Spotlight title | DM Sans | 13px | 500 | — |
| Spotlight meta | Merriweather italic | 11px | 400 | — |

### 8. Color reference

Uses only existing tokens from `static/css/tokens.css` plus the two theme-neutral rgba values for the solid bar state.

- Base tokens: `--color-text`, `--color-text-muted`, `--color-border`, `--color-surface`, `--color-accent` (and `--color-accent-hover` which we'll add: `#b56a4d` dark / `#9e5843` light)
- Transparent state (over hero): text rendered as explicit `#fff` and `rgba(255,255,255,{0.5,0.7,0.8})` regardless of theme — the hero imagery is the backdrop
- Solid state (scrolled):
  - Dark theme: `background: rgba(17,17,17,0.88)`, `backdrop-filter: blur(12px)`, border-bottom `1px solid var(--color-border)`
  - Light theme: `background: rgba(245,244,240,0.92)`, `backdrop-filter: blur(12px)`, border-bottom `1px solid var(--color-border)`

### 9. Spacing reference

- Desktop bar: `padding: 18px 26px`, `gap: 16px`, height ~80px
- Mobile bar: `padding: 14px 16px`, `gap: 8px`, height ~58px
- Dropdown: `padding: 6px` outer, `8px 10px` per item, `min-width: 180px`, `margin-top: 8px`
- Spotlight modal: `margin-top: 80px`, `max-width: 560px`, `padding: 16px` container, `8px 10px` per row
- Focus ring offset: 2px on all interactive elements

### 10. Motion

Reuses existing tokens (`--duration-fast: 150ms`, `--duration-normal: 200ms`, `--easing-default: ease` in tokens.css:27-29) plus two new additions:

- `--duration-surface: 250ms` (new) — scroll-aware state change
- `--duration-modal: 300ms` (new) — spotlight / dropdown open
- `--easing-measured: ease-in-out` (new) — applied to surface and modal transitions

Mapping:
- Bar surface state: 250ms ease-in-out on `background`, `backdrop-filter`, `border-color`
- Hover transitions: 200ms ease-out on color, background, border, box-shadow
- Dropdown / modal open: 300ms ease-in-out on opacity + `transform: scale(0.98 → 1)`
- Pick pill active: instant `scale(0.98)` via `:active` (no animation)
- `prefers-reduced-motion: reduce` — existing global rule (tokens.css:90-96) zeroes all transitions; no new overrides needed.

### 11. Scroll-aware surface behavior

- Implementation: `static/js/navbar-scroll.js` (~25 lines)
- `requestAnimationFrame`-throttled `window.addEventListener('scroll', …)`
- Threshold: `window.scrollY > 40` toggles `.navbar--solid` class on `<header class="navbar">`
- On page load, checks initial scroll position (handles browser back-button scroll restore)
- On pages without a hero (`/watched`, `/account`, `/login`, `/register`), the bar goes solid within the first few pixels of scroll (intentional)
- Exits cleanly on navigation — scroll listener only attached once per page load (no memory leak in SPAs, but this app is multi-page so not a concern)

### 12. Mobile compact bar

- Breakpoint: `<  768px` (existing Tailwind `md:` boundary)
- Layout: `[brand+tagline]` — `margin-left: auto` at `[⌕]` — `[Pick pill "Pick"]` — `[≡ hamburger]`
- Hamburger opens existing `.navbar-mobile-panel` (slide-down from top)
- Mobile panel contents (authenticated): Watched · Account · Theme (with inline `Dark ●` / `Light ○` state indicator, mirroring desktop dropdown) · Log Out
- Mobile panel contents (unauthenticated): Log In
- Mobile panel items use the same style as desktop dropdown items (DM Sans 600, 10.5px, UPPERCASE, 0.14em) — replaces the current `.navbar-mobile-links` treatment
- Mobile panel no longer includes "Pick a Movie" (it's on the bar) or a duplicate Search trigger (⌕ in the bar opens the same modal; modal renders full-width on mobile)
- Mobile panel styling unchanged from current — `.navbar-mobile-panel` (input.css:75-125)

## Backend delta

One new route and one new query method.

### `GET /api/search`

- Registered in `nextreel/web/routes/search.py` (new route module)
- Query param: `q` (string, required, min length 2 — shorter returns empty list)
- Returns JSON: `{"results": [{"tconst": "tt...", "title": "...", "year": 1994, "rating": 8.1}]}`
- Limit: 10 results
- Rate-limited via `infra/rate_limit.py` (existing) — bucket `"search_titles"` = 30 req/60s
- CSRF exempt (GET, no state mutation)
- Degrades gracefully — any DB failure returns 200 with empty results (prevents the UI from rendering a broken state mid-typing)
- Logs with lazy `%s`-formatting per project convention

### `MovieQueryBuilder.build_search_query(raw_query, limit=10)`

- New static method in `movies/query_builder.py`
- Returns `(sql, params)` tuple — follows the existing `build_*` pattern in the class
- Returns `(None, None)` for queries below the 2-character minimum so callers can short-circuit without hitting the DB
- Uses parameterized `%s` placeholders (LIKE with escaped wildcards). No f-string interpolation.
- Searches against **`movie_candidates.primaryTitle`** — the denormalized cache table populated by `refresh_movie_caches()` (see `infra/runtime_schema.py:136`). This table carries `primaryTitle`, `startYear`, and `averageRating` in typed columns, ideal for fast LIKE lookup.
- Director and poster columns are **not** returned — they live inside `movie_projection.payload_json` and would require per-result enrichment. The UI renders `"year · ★ rating"` in lieu of `"year · director"`. Posters use a consistent gradient placeholder in the Spotlight result rows.
- Orders by: exact title match → starts-with match → contains match, then by `averageRating` desc

## Scope boundaries

### Out of scope (explicit non-goals)
- `home.html` keeps its bespoke absolute-positioned top treatment (home-brand, home-login-link, home-theme-toggle). Not touched.
- Filter drawer (`templates/_filter_form.html`, `static/js/filter-drawer.js`, filter-drawer-tab button) unchanged.
- Floating `Previous` / `Next` `.arrow-btn` controls on the movie hero unchanged — they stay as hero overlays.
- No changes to avatar generation or `macros.html`'s `user_avatar()` helper.
- No theme picker — only a light↔dark toggle (matches existing `data-theme` cookie / localStorage pattern).
- No notification system, no recently-viewed history, no streaming-availability chips, no quick-filter chips, no keyboard-shortcut ribbon, no "Surprise me," no Letterboxd import reminder.
- No changes to logged-out registration flow — social sign-in + `/login` link unchanged.

### Affected files (summary)

| File | Change type |
|---|---|
| `templates/navbar_modern.html` | **Restructure** — brand-wrap with tagline, ⌕ icon button, updated mobile panel, include `_search_spotlight.html`, Watched promoted, avatar dropdown items revised |
| `templates/_search_spotlight.html` | **NEW** — modal markup |
| `static/css/input.css` | **Update** `.navbar-*` rules (lines 22-125); **add** `.navbar-brand-wrap`, `.navbar-tagline`, `.navbar-icon-btn`, `.navbar-pill`, `.navbar--solid`, `.search-spotlight-*` styles; **update** `.account-avatar-dropdown-menu` items list |
| `static/css/tokens.css` | **Add** `--duration-surface: 250ms`, `--duration-modal: 300ms`, `--easing-measured: ease-in-out`, `--color-accent-hover` (both light + dark theme blocks) |
| `static/js/navbar-scroll.js` | **NEW** (~25 lines) — scroll listener |
| `static/js/search-spotlight.js` | **NEW** (~80 lines) — open/close, `/` keybind, debounced fetch, keyboard nav |
| `nextreel/web/routes/movies.py` (or new `search.py`) | **ADD** `GET /api/search` route |
| `movies/query_builder.py` | **ADD** `MovieQueryBuilder.search_titles(query, limit=10)` |
| `static/css/output.css` | **Regenerate** via `npm run build-css` |

## Implementation phasing

Six independently shippable slices. Each leaves the app in a working state.

1. **CSS tokens + markup skeleton** — add new tokens, restructure `navbar_modern.html` (brand-wrap, tagline, icon button placeholder, promoted Watched), rewrite the `.navbar-*` CSS rules. Scroll-aware class wired but without listener. Visual check on a static page.
2. **Scroll listener** — `navbar-scroll.js`, 40px threshold, rAF throttle. Visual check: scroll `/movie` and `/watched`, confirm transparent→solid transition.
3. **Avatar dropdown content** — remove Watched (now top-level), add Theme toggle with inline state, restyle Log Out. Keyboard nav and focus trap verified.
4. **Backend search route** — `GET /api/search` + `MovieQueryBuilder.search_titles`. Curl validation: returns JSON, handles empty/short queries, rate-limited.
5. **Spotlight modal** — `_search_spotlight.html` + `search-spotlight.js`. ⌕ and `/` triggers wired, debounced fetch, keyboard nav, result navigation. Manual smoke test across desktop + mobile.
6. **Mobile pass** — compact bar layout (Pick always visible, text compressed), hamburger panel contents cleanup, Spotlight modal responsive check at 375px.

## Validation checklist

- Visual: `/movie`, `/watched`, `/account`, `/login`, `/register` in light + dark × transparent + scrolled = 20 combinations
- Keyboard: Tab through bar → Enter opens dropdown → arrows navigate → Esc closes; `/` anywhere opens Spotlight → type → ↓ → Enter → navigates
- Screen reader: VoiceOver announces "Search, button", "Pick a movie, button", "Account menu, button, collapsed/expanded", "Search dialog"
- Reduced motion: System toggle on — all transitions instant, confirm functionality unchanged
- Mobile viewport: 375px width manual sweep on all 5 pages
- Rate limit: hit `/api/search` repeatedly, verify 429 after threshold
- Logged-out: `/movie` as anonymous user shows correct logged-out layout, no 500s

## Open questions

None. All brainstorming decisions captured.

## Appendix — Brainstorming sessions

Mockups used during the brainstorming process are preserved in `.superpowers/brainstorm/` (gitignored) for reference:
- `01-purpose.html` — action bar vs masthead vs dashboard shelf
- `02-content.html`, `03-more-content.html`, `04-editorial-weight.html` — content manifest decisions
- `05-surface.html` — scroll-aware vs opaque vs transparent
- `06-layout.html` — search placement options
- `07-density.html` — 60 / 80 / 100px height variants
- `08-mobile.html` — 4 mobile layout patterns
- `09-motion.html` — motion character options
- `10-final.html` — consolidated four-state render
