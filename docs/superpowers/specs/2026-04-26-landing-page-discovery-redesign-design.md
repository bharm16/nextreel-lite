# Landing Page Discovery Redesign

**Date:** 2026-04-26
**Status:** Approved
**Replaces (partially):** `docs/superpowers/specs/2026-04-17-landing-page-redesign-design.md`. Sections superseded: layout (centered → side-by-side), title content (film title H1 → value-prop H1), in/out-of-scope (filter strip moves *into* scope; "hero only" no longer holds). Visual grammar (Bebas Neue display, real backdrop, Ken Burns, credit corner, grain overlay, sharp-corner CTAs, fade-up stagger) is **preserved** unchanged.

## Goal

Solve the comprehension problem on `/`: a cold visitor cannot tell what NextReel is from the current page (the H1 is the film's title, which reads as "this is a streaming site for [film X]"). Replace the centered hero with a side-by-side layout whose right column carries a value-prop headline, a one-line subtitle naming the exclusion-and-filter mechanic, the existing CTAs, and an interactive filter pill row that rerolls the hero in place. The film backdrop survives as proof — *here is what NextReel just picked* — but it is no longer the page's primary message.

The page serves both audiences (cold visitors and returning users) with the same content. No personalization strip, no auth-aware branching. The headline is calm enough ("A film you haven't seen. Every time you ask.") to read as a positioning statement on the 50th visit, not a sales pitch.

## Design Decisions Summary

| Dimension | Decision |
|-----------|----------|
| Auth handling | Path A pure — same content for both audiences |
| Layout (desktop) | Side-by-side: backdrop left half, content right half |
| Layout (mobile) | Stacked: backdrop 1:1 on top, content below; page scrolls |
| Hero film | Random READY row from `movie_projection`, respecting URL filter params |
| Backdrop | TMDb `backdrop_url` from payload, `original` size |
| H1 | Value-prop headline (NOT the film title) |
| Headline copy | *"A film you haven't seen. Every time you ask."* |
| Subtitle copy | *"Mark what you've seen. Filter the rest. Every pick is fresh."* |
| Kicker (above headline) | **Removed** (was `YOUR RANDOM FILM` in 2026-04-17) |
| Side label (rotated, on backdrop edge) | **Removed** (was `RANDOM · NO SIGN-UP` in 2026-04-17) |
| Primary CTA | `Pick another →` (white, sharp corners) — submits CSRF form |
| Secondary CTA | `See this film ↗` — links to `/movie/{public_id}` |
| Credit corner | `Film still: {title} ({year})` — preserved, bottom-left of backdrop half |
| Filter pill row | Active in-place reroll; URL-backed sticky state |
| Pills (hardcoded set) | `Drama` · `Comedy` · `1990s` · `< 120 min` · `7+ rating` · *More filters →* |
| Filter persistence | URL query params (`?genre=Drama&decade=1990s&...`) |
| In-place reroll | Client-side fetch of new landing film matching active filters |
| Empty state | Headline temporarily replaced with `No films match these filters.` + Clear-filters link |
| Personalization strip | **Excluded** — no auth-aware content |
| Scroll | Single frame on desktop; scrollable on mobile |
| Visual grammar | Bebas Neue, Ken Burns, grain, fade-up stagger — all preserved from 2026-04-17 |
| Out of scope | Letterboxd hook section; logged-in dashboard; auth-conditional rendering; filter dimension popovers |

## Sections

### 1. Hero frame (desktop ≥ 768px)

- Full-viewport (`100vh`) container, `position: relative; overflow: hidden;`
- `body` retains `overflow: hidden` on desktop only (`@media (min-width: 768px)`) — the landing is one frame
- Two-column flex: `display: flex;` on the hero container
  - **Left column** — `flex: 1; min-width: 0;` — backdrop half
  - **Right column** — `flex: 1; min-width: 0;` — content half

### 2. Backdrop column (left, desktop)

**Backdrop element**
- `<div class="landing-bg">` — `position: absolute; inset: 0; z-index: 0`
- `background-image: url({film.backdrop_url}); background-size: cover; background-position: center;`
- Ken Burns animation (preserved from 2026-04-17, unchanged): 40s `ease-in-out infinite alternate`, scaling 1.05 → 1.15 with -2% / -1% translate

**Gradient overlay**
- `<div class="landing-gradient">` at `z-index: 1`
- Same gradient as 2026-04-17: subtle top + bottom darkening for nav and credit legibility

**Film grain**
- `<div class="home-grain">` preserved from current implementation, `z-index: 2`, opacity 0.04

**Credit corner** (preserved from 2026-04-17)
- `<div class="landing-credit">Film still: {title} ({year})</div>`
- Positioned `bottom: 20px; left: 28px;` (was `right: 28px` in 2026-04-17 — moved to left because the right edge is no longer over the backdrop in side-by-side)
- Merriweather italic 9px, `color: rgba(255,255,255,0.45)`
- Year suffix dropped if year is `None`

**Removed from 2026-04-17**
- `landing-side-label` (the rotated `RANDOM · NO SIGN-UP` text on the far-left edge) — element and CSS deleted

### 3. Content column (right, desktop)

- `padding: 60px 56px 50px;`
- `display: flex; flex-direction: column; justify-content: center;`
- `background: var(--color-bg-deep, #0a0807);`
- `color: #fff;`
- `z-index: 4;`

**Headline**
- `<h1 class="landing-headline">A film you haven't seen.<br/>Every time you ask.</h1>`
- Font: `var(--font-display)` (Bebas Neue) — preserved from 2026-04-17
- Size: `clamp(40px, 5vw, 72px)` (smaller than the 2026-04-17 `clamp(64px, 12vw, 148px)` because the right column is half-width on desktop)
- `font-weight: 400; line-height: 0.92; letter-spacing: 0.01em;`
- `text-shadow: 0 2px 30px rgba(0,0,0,0.5);`
- `text-transform: uppercase;`
- `margin: 0 0 18px;`

**Subtitle**
- `<p class="landing-sub">Mark what you've seen. Filter the rest. Every pick is fresh.</p>`
- DM Sans 14px, `line-height: 1.55`, `color: rgba(255,255,255,0.82)`
- `max-width: 360px; margin: 0 0 26px;`

**Action row** (preserved from 2026-04-17)
- Flex row, `gap: 10px; margin-bottom: 24px;`

**Primary CTA**
- `<form method="POST">` wrapping a `<button class="landing-cta-primary">Pick another →</button>`
- The form's `action` is **conditional**:
  - When no filter params are active: `action="/next_movie"` (existing route)
  - When filter params are active: `action="/filtered_movie"` (existing route) with hidden inputs mirroring URL state — see Section 6 (Backend delta)
- Same visual styling as 2026-04-17 (`background: #fff; color: #0a0807; padding: 12px 22px; border-radius: 0;`)
- Includes hidden `csrf_token` input

**Secondary CTA**
- `<a class="landing-cta-ghost" href="{{ movie_url(landing_film) }}">See this film ↗</a>`
- Same styling as 2026-04-17

**Filter pill row**
- `<div class="landing-pills" role="group" aria-label="Quick filters">` below the action row
- `padding-top: 20px; border-top: 1px solid rgba(255,255,255,0.08);`
- Six children:

| Order | Label | URL param | Active state |
|---|---|---|---|
| 1 | `Drama` | `genre=Drama` | white-filled with `×` icon |
| 2 | `Comedy` | `genre=Comedy` | white-filled with `×` icon |
| 3 | `1990s` | `decade=1990s` | white-filled with `×` icon |
| 4 | `< 120 min` | `runtime=lt120` | white-filled with `×` icon |
| 5 | `7+ rating` | `rating=7plus` | white-filled with `×` icon |
| 6 | `More filters →` | (link) | always plain link, never active state |

- Pill markup: `<button type="button" class="landing-pill" data-filter-key="genre" data-filter-value="Drama" aria-pressed="false">Drama</button>`
- Active state: `aria-pressed="true"`, white background, `color: #0a0807`, an inline `<span>` with `×` after the label
- "More filters →" is `<a href="{{ movie_url(landing_film) }}">More filters →</a>` — routes to the inline filter form on the movie detail page (existing UX per `2026-04-16-remove-set-filters-page-design.md`)

**Pill styling**
- `border: 1px solid rgba(255,255,255,0.32); border-radius: 999px;`
- `padding: 6px 13px; font-size: 10px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase;`
- `color: rgba(255,255,255,0.92);`
- Active: `background: #fff; color: #0a0807; border-color: #fff;`
- Hover: `border-color: rgba(255,255,255,0.6); background: rgba(255,255,255,0.04);`
- Focus: `outline: 2px solid var(--color-accent); outline-offset: 2px;`

**Genre two-pill case** — only one of the genre pills (Drama or Comedy) can be active at a time. Clicking the other replaces the URL `genre=` param. This matches the existing filter system, which treats genre as a single-select choice on the landing strip (the full filter UI on movie detail allows multi-select).

### 4. Mobile (`< 768px`)

- Hero container becomes `display: block; min-height: auto;`
- `body` overflow on mobile: `overflow-y: auto;` (page scrolls)
- Backdrop column becomes a top block: `aspect-ratio: 1/1; flex: none;`
  - Side label removed (already gone on desktop)
  - Credit corner stays at bottom-left
  - Ken Burns animation continues on mobile (does not depend on viewport size)
- Content column becomes a bottom block: `padding: 18px 20px 22px; display: block;`
  - Headline `font-size: 28px`
  - Subtitle `font-size: 11px`
  - Action row: `flex-direction: column; gap: 8px;` — CTAs stack full-width up to `max-width: 320px`
  - Pill row: `flex-wrap: wrap; gap: 5px; padding-top: 12px;`
  - Pills shrink to `padding: 4px 10px; font-size: 8.5px;`
- Reduced-motion: existing global rule in `tokens.css:90-96` zeroes Ken Burns and fade-up — no new rules needed

### 5. Filter system

#### URL schema

Active filters are encoded as URL query parameters. The schema is intentionally narrower than the full filter UI's form schema — these five params are all the landing strip can produce.

| URL param | Allowed values | Maps to internal criteria |
|---|---|---|
| `genre` | One of `VALID_GENRES` (case-sensitive match against `movies.filter_parser.VALID_GENRES`) | `criteria["genres"] = [value]` |
| `decade` | One of `1970s`, `1980s`, `1990s`, `2000s`, `2010s`, `2020s` | `criteria["min_year"] = N0; criteria["max_year"] = N9` (e.g. `1990s` → 1990–1999) |
| `runtime` | `lt90` → `criteria["max_runtime"] = 90`<br/>`lt120` → `criteria["max_runtime"] = 120`<br/>`gt150` → `criteria["min_runtime"] = 150` | (per left column) — only `lt120` is rendered on the landing strip; `lt90` and `gt150` are reserved values the URL parser accepts for future expansion |
| `rating` | `6plus` → `criteria["min_rating"] = 6.0`<br/>`7plus` → `criteria["min_rating"] = 7.0`<br/>`8plus` → `criteria["min_rating"] = 8.0` | (per left column) — only `7plus` is rendered on the landing strip; `6plus` and `8plus` are reserved |

Invalid values are silently dropped (the URL is treated as if the bad param weren't present). This avoids returning errors on malformed shared links.

#### Pill activation

On page load, the route handler reads query params and computes which pills should render in their active state. Active pills get `aria-pressed="true"` plus the `×` indicator.

Active pills can be deactivated by clicking them — the URL param drops, the page rerolls.

#### Mutual-exclusion rules

- `genre=Drama` and `genre=Comedy` are mutually exclusive — clicking the inactive one replaces the active one
- All other dimensions are independently toggleable
- Multiple dimensions can be active simultaneously (`?genre=Drama&decade=1990s&runtime=lt120` → 1990s drama films under 120 minutes)

#### Empty state

When the active filter combination yields no results:
- Server-side (initial load): `landing_film` is `None` → template renders an empty-state variant of the hero
- Client-side (after pill click): `/api/landing-film` returns 204 → JS swaps to the empty-state DOM
- Visual:
  - Backdrop becomes a subdued fallback (`/static/img/backdrop-placeholder.svg`)
  - Credit corner is hidden (no film to credit)
  - Headline replaced with `No films match these filters.`
  - Subtitle replaced with `Try removing one.`
  - Primary CTA becomes `<a class="landing-cta-primary" href="/">Clear filters</a>` — a plain link (not a form), since clearing has no side effect
  - Secondary CTA (`See this film ↗`) is hidden — there is no film
  - Pills remain visible and remain clickable; clicking any active pill (which deactivates it) leaves the empty state if other filters still produce no results, or re-fetches a film if the new combination matches
- Reverts on next successful pill toggle (any state where `/api/landing-film` returns 200)

### 6. Backend delta

#### `movies/landing_film_service.py` — extend `fetch_random_landing_film`

Add a `criteria: dict | None = None` keyword arg. When `criteria` is `None`, current behavior (fast offset-based random pick from all READY rows). When `criteria` is provided, fall through to a filter-aware path:

```python
async def fetch_random_landing_film(
    pool, criteria: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    if not criteria:
        return await _fetch_random_unfiltered(pool)
    return await _fetch_random_filtered(pool, criteria)
```

The filter-aware path:
1. Build a parameterized SQL using `MovieQueryBuilder` (existing in `movies/query_builder.py`) to constrain `movie_projection` to rows whose payload metadata matches the criteria. Specifically:
   - `genres` filter: matches against `movie_candidates.genres` joined on `tconst`, since `movie_projection.payload_json` doesn't index well for genre. Subquery: `tconst IN (SELECT tconst FROM movie_candidates WHERE FIND_IN_SET(%s, genres))`
   - Year range: matches `JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.year')) BETWEEN %s AND %s` (lossy — year is stored as a string in payload; cast in SQL)
   - `max_runtime`: matches against the runtime in candidate row, not payload (payload runtime is a formatted string)
   - `min_rating`: matches `movie_candidates.averageRating >= %s`
2. Run the offset-based fast pick against this filtered set.
3. Backdrop validation step is unchanged (Python-side `https://image.tmdb.org/` prefix check on `payload_json.backdrop_url`).
4. Returns same dict shape as the unfiltered path — caller is unchanged.

The READY-row count cache must NOT be reused for filtered queries (the count differs per filter combination). For filtered queries, accept a cold COUNT — landing-strip clicks are user-paced, not bursty, and the count is small enough on a filtered subset to run unmemoized.

#### New endpoint: `GET /api/landing-film`

In `nextreel/web/routes/movies.py`:

```python
@bp.route("/api/landing-film")
async def landing_film_json():
    services = _services()
    criteria = _criteria_from_query_args(request.args)
    film = await fetch_random_landing_film(services.movie_manager.db_pool, criteria)
    if film is None:
        return ("", 204)
    if not film.get("public_id"):
        film["public_id"] = await public_id_for_tconst(
            services.movie_manager.db_pool, film["tconst"]
        )
    return jsonify(film)
```

Where `_criteria_from_query_args` is a small helper (in the same file or `movies/landing_filter_url.py`) that translates the URL schema (`genre`, `decade`, `runtime`, `rating`) into the internal criteria dict, dropping any invalid values silently.

#### `home()` route — read URL params on initial load

```python
@bp.route("/")
async def home():
    state = _current_state()
    services = _services()
    data = await services.movie_manager.home(state, legacy_session=_legacy_session())

    criteria = _criteria_from_query_args(request.args)
    landing_film = await fetch_random_landing_film(
        services.movie_manager.db_pool, criteria
    )
    if landing_film is None:
        landing_film = (
            random.choice(_LANDING_FALLBACK_POOL) if not criteria else None
        )

    active_filters = _active_filters_for_template(criteria)

    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
        landing_film=landing_film,
        active_filters=active_filters,
    )
```

When `criteria` is non-empty and the query returns no rows, **skip the fallback pool** — the user explicitly filtered, so showing one of three hardcoded fallback films would lie. Render the empty state instead (`landing_film=None` is the empty-state signal).

#### Primary-CTA form submission

When any URL filter params are active, the primary CTA form must POST to `/filtered_movie` with hidden inputs that mirror the URL state. Otherwise it submits to `/next_movie` as today.

The template logic:

```jinja
{% if active_filters %}
<form method="POST" action="/filtered_movie">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  {% for key, value in active_filters.items() %}
    <input type="hidden" name="{{ key }}" value="{{ value }}">
  {% endfor %}
  <button type="submit" class="landing-cta-primary">Pick another →</button>
</form>
{% else %}
<form method="POST" action="/next_movie">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <button type="submit" class="landing-cta-primary">Pick another →</button>
</form>
{% endif %}
```

`active_filters` here uses the *form schema* keys expected by `/filtered_movie` (e.g. `genre`, `min_year`, `max_year`, `max_runtime`, `min_rating`), not the URL schema keys. The `_active_filters_for_template` helper produces this mapping.

### 7. Client-side JavaScript

A single small module in `static/js/landing-pills.js` (~120 lines, vanilla JS — no framework dependency):

**Responsibilities**
1. Wire click handlers on `.landing-pill[data-filter-key]` buttons
2. On click: toggle that filter in the URL via `history.pushState`, then fetch `/api/landing-film` with the new params
3. On 200 response: update DOM (background-image on `.landing-bg`, text in `.landing-credit`, href on `.landing-cta-ghost`, hidden inputs in `.landing-cta-primary` form, `aria-pressed` and visual state on the pill row)
4. On 204 response: render empty-state DOM (replace headline + subtitle, swap CTA labels, set fallback backdrop)
5. Handle popstate (browser back/forward) — re-read URL params, re-fetch, update DOM, *without* pushing a new history entry
6. Loading state: subtle backdrop fade-out before fetch, fade-in on response (~200ms)

**Mutual exclusion** — the genre pair (Drama, Comedy): if user clicks Comedy while Drama is active, the JS first removes `genre=Drama` from the URL state before adding `genre=Comedy`.

**Initial state** — JS reads `URLSearchParams` on `DOMContentLoaded` to set initial `aria-pressed` on already-active pills (server already rendered them in active state, so this is a no-op consistency check; it matters if the user navigates with back/forward).

**No external dependencies.** No bundler, no transpiler. Loaded via `<script type="module" src="{{ url_for('static', filename='js/landing-pills.js') }}" defer></script>` from `home.html`.

### 8. Typography

| Element | Font | Size | Weight | Case / Tracking |
|---|---|---|---|---|
| Headline | Bebas Neue | `clamp(40px, 5vw, 72px)` desktop · 28px mobile | 400 | UPPERCASE, 0.01em |
| Subtitle | DM Sans | 14px desktop · 11px mobile | 400 | Sentence case, normal tracking |
| Primary CTA | DM Sans | 11px desktop · 8.5px mobile | 700 | UPPERCASE, 0.22em |
| Secondary CTA | DM Sans | 11px desktop · 8.5px mobile | 700 | UPPERCASE, 0.22em |
| Pill | DM Sans | 10px desktop · 8.5px mobile | 600 | UPPERCASE, 0.12em |
| Credit corner | Merriweather italic | 9px | 400 | As-written |

The kicker and side label rows from 2026-04-17 are deleted; their `.landing-kicker`, `.landing-kicker-dot`, and `.landing-side-label` styles can be removed from `static/css/input.css`.

### 9. Color reference

Reuses tokens from 2026-04-17 — no new tokens needed.

- Headline: `#fff`
- Subtitle: `rgba(255,255,255,0.82)`
- Pill (idle): `rgba(255,255,255,0.92)` text, `rgba(255,255,255,0.32)` border
- Pill (active): `#0a0807` text on `#fff` background
- Pill (hover): `rgba(255,255,255,0.04)` background, `rgba(255,255,255,0.6)` border
- Credit corner: `rgba(255,255,255,0.45)`
- Right column background: `var(--color-bg-deep, #0a0807)`

### 10. Motion

- Ken Burns on backdrop: **unchanged** from 2026-04-17 (40s loop, scaling 1.05 → 1.15 with translate)
- Content fade-up on initial load: **unchanged** from 2026-04-17 (staggered delays on headline, subtitle, action row, pill row)
- Filter reroll animation: **new** — a 180ms backdrop fade-out before fetch, 220ms fade-in on response
- Reduced-motion: existing global rule zeroes all three; the JS must also respect `prefers-reduced-motion: reduce` and skip the fade animations (set CSS class only — no inline transition on those frames)

### 11. Accessibility

- Headline is the page's primary `<h1>` — preserved as the document title's accessible name
- Pill buttons use `<button type="button">` with `aria-pressed="true|false"` — screen readers announce toggle state correctly
- The pill row has `role="group" aria-label="Quick filters"` — explains the row's purpose
- Active pills have a textual `×` (not just visual) so screen readers announce "Drama, pressed, ×"
- Active pill click target includes the `×` — there is no separate close button
- Empty state announces via `aria-live="polite"` on the headline container so screen readers hear "No films match these filters." after a filter click
- Focus rings on pills, CTAs, and More-filters link use the existing `2px solid var(--color-accent)` at 2px offset
- Backdrop has no `alt` attribute (it's `background-image`, not `<img>`); credit corner provides attribution

### 12. Responsive behavior

| Breakpoint | Layout | Backdrop | Content | Pills | Scroll |
|---|---|---|---|---|---|
| ≥ 768px | Side-by-side flex | Left half, Ken Burns | Right half, vertically centered | Single row, may wrap | `body { overflow: hidden }` |
| < 768px | Stacked block | Top, 1:1 aspect ratio | Below, padding 18/20/22 | Multi-row wrap | `body { overflow-y: auto }` |

## Scope boundaries

### Out of scope (explicit non-goals)

- **Letterboxd hook section** — discoverability of the import flow stays in the navbar / account area
- **Personalization strip** — no auth-aware hero content; logged-in users see the same page
- **Logged-in dashboard** — Path B was considered and rejected
- **Featured-film curation** (Criterion's "This Week" model) — already rejected in 2026-04-17, still rejected
- **Press quotes / festival laurels / critic attribution** — already rejected, still rejected
- **Kicker text above the headline** — removed; the headline starts the right column directly
- **Side label** (rotated text on backdrop edge) — removed
- **Filter dimension popovers** (chip → dropdown) — pills are direct, single-click apply only
- **Filter pills with multi-select within one dimension** (e.g. selecting Drama + Comedy together) — landing strip is single-select per dimension; full filter UI on movie detail handles multi-select
- **Animation of the pill press itself** beyond the backdrop fade — no pill scale/glow effects
- **Newsletter signup, "how it works," collections, essays** — none of these surfaces appear

### Affected files

| File | Change type |
|---|---|
| `templates/home.html` | **Rewrite** — new side-by-side body markup, new pill row, conditional form action, empty-state branch |
| `nextreel/web/routes/movies.py` | **Modify** — extend `home()` to read URL params, add `/api/landing-film` endpoint, add `_criteria_from_query_args` and `_active_filters_for_template` helpers |
| `movies/landing_film_service.py` | **Modify** — extend `fetch_random_landing_film` with optional `criteria` arg and a filter-aware fetch path |
| `movies/landing_filter_url.py` | **New** (optional) — URL schema → internal criteria translation, if not inlined into the route |
| `static/js/landing-pills.js` | **New** — vanilla JS for pill clicks, fetch, DOM update, history API |
| `static/css/input.css` | **Modify** — new side-by-side flex layout, pill row styles, mobile breakpoint, empty-state styles, removal of `.landing-kicker*` and `.landing-side-label` rules |
| `static/css/output.css` | **Regenerate** via `npm run build-css` |

No changes to the navbar, routing for `/movie/<id>`, auth, session, or any other template.

## Implementation phasing

Five independently shippable slices.

1. **Backend extension to `fetch_random_landing_film`** — add optional `criteria` arg, the filter-aware SQL path, and the `_criteria_from_query_args` translation helper. Unit tests for criteria translation and for filtered/empty/unfiltered fetch paths. *Validation: pytest passes; ad-hoc curl with criteria returns expected films.*
2. **`/api/landing-film` endpoint** — wire the new endpoint into the routes blueprint. *Validation: `curl /api/landing-film?genre=Drama` returns a JSON film dict; `curl /api/landing-film?genre=NotAGenre` returns 204 (since the bad genre is dropped silently AND no other criteria narrow the set).*
3. **`home()` route URL-aware initial render** — read URL params, render active pills server-side, conditional CTA form action. *Validation: hit `/?genre=Drama` and inspect rendered HTML — Drama pill is `aria-pressed="true"`, primary CTA form action is `/filtered_movie` with hidden `genre=Drama` input.*
4. **CSS + template rewrite** — new side-by-side flex layout, pill row markup, mobile breakpoint, empty-state branch. *Validation: visual inspection at 1920 / 1200 / 768 / 375 widths.*
5. **Client-side JS** — `landing-pills.js`, click handlers, fetch, DOM swap, history pushState, reduced-motion respect. *Validation: click each pill, observe URL update and backdrop reroll; click active pill to deactivate; click "More filters →" to leave page; use browser back button to return to prior filter state.*

## Validation checklist

- Visual: load `/` 10 times — 10 different films render
- Visual: load `/?genre=Drama` 10 times — 10 different drama films render
- Visual: load `/?genre=NotAGenre` — silently treats as no filter, renders any film
- Visual: load `/?genre=Drama&decade=1990s` — 1990s drama films
- Pill: click Drama (idle) → URL becomes `/?genre=Drama`, backdrop changes to drama, Drama pill becomes active with `×`
- Pill: click Comedy while Drama is active → URL becomes `/?genre=Comedy`, Drama pill goes idle, Comedy goes active
- Pill: click Drama (active) → URL drops `genre=Drama`, backdrop changes back to any film, Drama goes idle
- Pill: combine `genre=Drama` + `decade=1990s` + `runtime=lt120` + `rating=7plus` — backdrop shows a 1990s drama under 120 minutes with 7+ rating
- Empty: filter combo with no matches → headline becomes "No films match these filters.", primary CTA becomes "Clear filters"
- Browser back/forward: navigate Drama → Comedy → Drama via pill clicks, then use browser back twice — URL and active pills walk back through the history correctly
- Primary CTA: with no filters active, submits to `/next_movie` and lands on a movie page; with filters active, submits to `/filtered_movie` with hidden inputs and lands on a filtered movie page
- Secondary CTA: "See this film ↗" routes to a valid `/movie/{public_id}` page
- "More filters →" link: routes to the inline filter UI on the movie detail page (the current film's `/movie/{public_id}` page)
- Mobile: 375px width — backdrop is square (1:1), content stacks below, pills wrap, CTAs stack vertically, no horizontal overflow
- Reduced motion: System Settings toggle — Ken Burns frozen, fade-up frozen, backdrop reroll fade frozen
- Keyboard: Tab through navbar → headline (skipped, no focus) → primary CTA → secondary CTA → each pill → "More filters →" — all visible focus rings
- Screen reader: pill state announced as "Drama, pressed" / "Drama, not pressed"
- Accessibility: empty-state headline announced via `aria-live` on the next filter change
- No console errors on the route, no 4xx/5xx for `/` or `/api/landing-film`, no broken image URLs on a sample of 20 picks

## Open questions

None. All brainstorming decisions captured.

## Appendix — brainstorming session

Visual mockups produced during the brainstorm are preserved at `.superpowers/brainstorm/79729-1777252349/content/` (gitignored) for reference:

- `00-welcome.html` — session orientation
- `01-layout-shape.html` — centered vs side-by-side; side-by-side selected
- `02-headline-copy.html` — six headline candidates; #1 selected
- `03-subtitle.html` — five subtitle candidates; #1 selected
- `04-filter-strip.html` — none / passive / active strip; active (C) selected
- `05-filter-dimensions.html` — top-genre / mixed / dimension-chip; mixed (B) selected
- `06-personalization.html` — four strip variants; rejected entirely
- `07-final-composition-v3.html` — final desktop + mobile composition with all changes (no kicker, no side label, mobile backdrop 1:1)

Decision log (in dialog order):

1. Reversal of 2026-04-17 is intentional (trigger: comprehension fear — cold visitors can't tell what NextReel is)
2. Hero feature is the exclusion mechanic (mark watched → exclude → fresh suggestions); filtering is a sidekick
3. Page serves both audiences equally; `/` is the default landing path for both
4. Path A pure (no personalization, no auth-aware branching)
5. Side-by-side layout; backdrop left, content right
6. Headline #1: "A film you haven't seen. Every time you ask."
7. Subtitle #1: "Mark what you've seen. Filter the rest. Every pick is fresh."
8. Filter strip: active (in-place reroll, JS-driven)
9. Filter persistence: sticky, URL-backed
10. Filter pills: balanced mix (Drama, Comedy, 1990s, < 120 min, 7+ rating, More filters →)
11. Personalization strip: rejected entirely (Path A pure confirmed)
12. Polish: kicker removed, side label removed, mobile backdrop 1:1, mobile content tight
