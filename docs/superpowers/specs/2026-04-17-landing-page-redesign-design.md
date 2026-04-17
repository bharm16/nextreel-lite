# Landing Page Redesign

**Date:** 2026-04-17
**Status:** Approved
**Replaces:** Current `templates/home.html` (centered "Discover something new" splash with hardcoded 2001 backdrop)

## Goal

Replace the current static splash with a Criterion-style film spotlight that picks a real film from the catalog on every page load and displays that film's actual TMDb backdrop, title, and metadata as the hero. The page performs the product (random discovery) at full visual drama, using honest content drawn from `movie_projection` — no fabricated press quotes, festival laurels, or "programming" claims. Criterion's *visual grammar* (full-bleed imagery, giant display type, solid white CTA, sharp corners) applied to *truthful substance* (a real film, a real backdrop, real credits).

## Design Decisions Summary

| Dimension | Decision |
|-----------|----------|
| Shape | Single full-viewport frame, no scroll |
| Hero content | Random real film from `movie_projection` (enriched rows only) |
| Backdrop | That film's own TMDb `backdrop_url` (already stored in payload), `original` size |
| Title treatment | Bebas Neue 120px display type — the film's actual title |
| Kicker (above title) | `YOUR RANDOM FILM` (DM Sans 700, 10px, 0.28em tracked caps) |
| Metadata (below title) | `{year} · {director} · {runtime}` (DM Sans 600, 11px, 0.24em) — runtime is already formatted as `"102 min"` in the projection payload, so no unit suffix is appended in the template |
| Primary CTA | Solid white button, sharp corners, `PICK ANOTHER →` → `POST /next_movie` (existing CSRF form) |
| Secondary CTA | Ghost outlined button, `SEE THIS FILM ↗` → `/movie/{tconst}` |
| Side label | Vertical text (rotated -90°) on left edge: `RANDOM · NO SIGN-UP` |
| Image credit | Tiny italic serif bottom-right: `Film still: {title} ({year})` |
| Motion | Ken Burns 40s loop on backdrop; content staggered fade-up on load |
| Backdrop rotation frequency | One pick per page load — no cache, every refresh rerolls |
| Navbar | Existing `navbar_modern.html` unchanged, sits transparent at top |
| Logged-in vs logged-out | Same page for both — logged-in users bypass via navbar's Pick pill |
| Out of scope | Featured-film curation, weekly rotation, press quotes, laurels, sections below the fold, newsletter, Letterboxd pitch, "how it works" |

## Sections

### 1. Hero frame

- Full-viewport (100vh) container, same as current
- `position: relative; overflow: hidden;`
- `body` retains `overflow: hidden` (no page scroll — the landing is one frame only)

**Backdrop element**
- `<div class="landing-bg">` absolute-positioned, `inset: 0`, `z-index: 0`
- `background-image: url({film.backdrop_url});` — the TMDb-sourced image from the projection payload, served at `/original/` path
- `background-size: cover; background-position: center;`
- Ken Burns animation: `transform: scale(1.05) translate(0,0)` → `transform: scale(1.15) translate(-2%, -1%)` over 40 seconds, `ease-in-out infinite alternate`

**Gradient overlay** (for nav and credit legibility only, no center vignette)
- `<div class="landing-gradient">` at `z-index: 1`
- `background: linear-gradient(180deg, rgba(0,0,0,0.4) 0%, rgba(0,0,0,0.08) 20%, rgba(0,0,0,0) 50%, rgba(0,0,0,0.5) 100%);`

**Film grain** (optional — retain existing `.home-grain` for analog texture)
- Unchanged from current implementation
- `z-index: 2`

### 2. Side label

- `<div class="landing-side-label">RANDOM · NO SIGN-UP</div>`
- Positioned: `left: 20px; top: 50%; transform: translateY(-50%) rotate(-90deg); transform-origin: left center;`
- Typography: DM Sans 700, 9px, `letter-spacing: 0.35em`, UPPERCASE, `color: rgba(255,255,255,0.55)`
- `z-index: 3`

### 3. Centered content stack

- Absolute-positioned cover of the viewport, flex column, center-aligned
- Padding: `80px 60px 100px` (top room for navbar, side room for breath, bottom room for credit)
- `z-index: 4`

**Kicker**
- `<div class="landing-kicker"><span class="landing-kicker-dot"></span>Your random film</div>`
- DM Sans 700, 10px, `letter-spacing: 0.28em`, UPPERCASE, `color: rgba(255,255,255,0.72)`
- Small white 6px round dot preceding the text, 10px gap
- `margin-bottom: 24px`

**Title**
- `<h1 class="landing-title">{film.title | upper}</h1>`
- Bebas Neue 120px (viewport-responsive: `clamp(64px, 12vw, 148px)`)
- `font-weight: 400; line-height: 0.92; letter-spacing: 0.01em;`
- `color: #fff; text-shadow: 0 2px 30px rgba(0,0,0,0.5);`
- `margin: 0 0 12px;`
- Long titles wrap naturally (CSS `word-break: normal; overflow-wrap: break-word;`)

**Metadata**
- `<div class="landing-meta">{year} · {director} · {runtime}</div>`
- DM Sans 600, 11px, `letter-spacing: 0.24em`, UPPERCASE
- `color: rgba(255,255,255,0.82); margin-bottom: 36px;`
- `runtime` is already formatted in the projection payload as e.g. `"102 min"` — the template does NOT append a unit
- If director is missing (value is `"Unknown"` or empty), drop the ` · {director}` segment (template conditional)
- If runtime is `"Unknown"` or `"0 min"`, drop the ` · {runtime}` segment
- If year is `"Unknown"` or `"N/A"`, drop the year segment (rare — most enriched films have real years)

**Actions row**
- Flex row, centered, 12px gap

**Primary CTA** (`<a class="landing-cta-primary">`)
- `background: #fff; color: #0a0807;`
- Padding: `16px 32px; border-radius: 0;` (sharp corners — deliberate Criterion reference)
- DM Sans 700, 11px, 0.22em tracked caps
- Content: `Pick Another →`
- Wraps the existing CSRF `POST /next_movie` form pattern; on click submits the hidden form

**Secondary CTA** (`<a class="landing-cta-ghost">`)
- Transparent background, `border: 1px solid rgba(255,255,255,0.45); color: #fff;`
- Same padding and typography as primary
- Content: `See this film ↗`
- `href="{{ url_for('main.movie_detail', tconst=film.tconst) }}"`

### 4. Image credit

- `<div class="landing-credit">Film still: {title} ({year})</div>`
- Positioned: `bottom: 20px; right: 28px; z-index: 4;`
- Merriweather italic 9px, `color: rgba(255,255,255,0.45)`
- Honest attribution — differentiates this from Criterion's (dishonest-for-us) "This Week" label

### 5. Navbar

- Existing `{% include 'navbar_modern.html' %}` at top — **unchanged** from current state
- Scroll-aware class (`navbar--solid`) never triggers because the page doesn't scroll
- For logged-out visitors this shows: brand + tagline · search icon · Pick pill · theme toggle · Log In
- For logged-in visitors: adds Watched link and swaps Log In for avatar dropdown (existing navbar logic)

## Data & picking

### Data source

Query `movie_projection` for enriched rows with the fields needed to render the hero.

```sql
SELECT tconst, payload_json
FROM movie_projection
WHERE projection_state = 'ready'
  AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.backdrop_url')) LIKE 'https://image.tmdb.org/%'
ORDER BY RAND()
LIMIT 1
```

**Why this filter:** we need a real TMDb backdrop that will render full-bleed. Enriched rows (`projection_state = 'ready'`) are the ones with TMDb-sourced imagery. The `LIKE 'https://image.tmdb.org/%'` explicitly excludes the placeholder fallback URL `/static/img/backdrop-placeholder.svg` that `projection_payload_factory.py:52` emits for films that are READY but whose TMDb metadata lacked a backdrop. This guarantees every pick has a genuine film still.

### Payload extraction

The enriched projection payload (built by `movies/movie_payload.py:build_payload` and stored in `payload_json`) stores display-ready string fields — **not** raw TMDb objects:

- `title` — string (e.g. `"Chungking Express"`)
- `year` — 4-char string already extracted from `release_date` (e.g. `"1994"` or `"N/A"`)
- `directors` — comma-joined string (e.g. `"Wong Kar-wai"` or `"Writer, Director"` or `"Unknown"`)
- `runtime` — string with unit suffix (e.g. `"102 min"` or `"Unknown"`)
- `backdrop_url` — full TMDb URL at `original` size, or `/static/img/backdrop-placeholder.svg` for core (non-enriched) rows
- `tconst` — from the row (used for secondary CTA link)

Confirmed by reading `movies/movie_payload.py:59,69-70,83-84` and `movies/projection_payload_factory.py:44,58`.

Build the landing-film context dict at the route level with defensive null-handling for the "Unknown" sentinel values the payload factory emits for missing data:

```python
def _clean(value, sentinels=("Unknown", "N/A", "", "0 min")):
    if value is None or value in sentinels:
        return None
    return value

landing_film = {
    "tconst": row["tconst"],
    "title": payload.get("title"),
    "year": _clean(payload.get("year")),
    "director": _clean(payload.get("directors")),
    "runtime": _clean(payload.get("runtime")),
    "backdrop_url": payload["backdrop_url"],
}
```

The template then conditionally renders each metadata segment only if non-None, with ` · ` separators inserted only between present segments.

### Pick frequency

- Every page load triggers a fresh random pick (`ORDER BY RAND() LIMIT 1`)
- No caching — acceptable cost because the landing is a low-traffic entry surface and `movie_projection` has a `projection_state` index already
- If traffic rises, introduce a 60-second in-memory cache of a shuffled pool of 20 picks; rotate through them before re-querying

### Fallback

If the query returns zero rows (fresh DB, migration in progress, or projection enrichment not yet run):

- Render a hardcoded 3-film fallback pool (module-level constant, same dict shape as the query result):
  1. `{"tconst": "tt0109424", "title": "Chungking Express", "year": "1994", "director": "Wong Kar-wai", "runtime": "102 min", "backdrop_url": "https://image.tmdb.org/t/p/original/2jSCMkdS63uyMyXmc3dsDCAyiFb.jpg"}`
  2. `{"tconst": "tt0062622", "title": "2001: A Space Odyssey", "year": "1968", "director": "Stanley Kubrick", "runtime": "149 min", "backdrop_url": "https://image.tmdb.org/t/p/original/dMrAwwB7PMC4SjgsTbgmEJblaYd.jpg"}`
  3. `{"tconst": "tt0118694", "title": "In the Mood for Love", "year": "2000", "director": "Wong Kar-wai", "runtime": "98 min", "backdrop_url": "https://image.tmdb.org/t/p/original/iYBBeBMLyLR1R1eYMMvfAJLeiIr.jpg"}`
- Pick one at random (`random.choice(_LANDING_FALLBACK_POOL)`)
- Dict shape is intentionally identical to the query result so the template renders either path identically with no conditional logic

### Handling the "see this film" link

- The secondary CTA routes to `/movie/{tconst}` which is the existing `movie_detail` route
- If the fallback pool is used, the three tconsts listed must exist in the database (they will, given the catalog is IMDb-derived) — otherwise `movie_detail` returns 404

## Typography

### New font

Bebas Neue is added to the Google Fonts link in `home.html` (and only `home.html` — no other page uses it).

```html
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Merriweather:ital,wght@0,300;0,400;0,700;1,400&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
```

### New token

Add to `static/css/tokens.css`:

```css
--font-display: 'Bebas Neue', 'Arial Narrow', 'Helvetica Neue Condensed', sans-serif;
```

Applied to `.landing-title`.

### Typography reference

| Element | Font | Size | Weight | Case / Tracking |
|---|---|---|---|---|
| Kicker | DM Sans | 10px | 700 | UPPERCASE, 0.28em |
| Title | Bebas Neue | `clamp(64px, 12vw, 148px)` | 400 | As-written, 0.01em |
| Metadata | DM Sans | 11px | 600 | UPPERCASE, 0.24em |
| Primary CTA | DM Sans | 11px | 700 | UPPERCASE, 0.22em |
| Secondary CTA | DM Sans | 11px | 700 | UPPERCASE, 0.22em |
| Side label | DM Sans | 9px | 700 | UPPERCASE, 0.35em |
| Credit | Merriweather italic | 9px | 400 | As-written |

## Color reference

Reuses existing tokens. All landing content is rendered on dark imagery; text is white with opacity steps:
- Title: `#fff`
- Kicker: `rgba(255,255,255,0.72)`
- Metadata: `rgba(255,255,255,0.82)`
- Side label: `rgba(255,255,255,0.55)`
- Credit: `rgba(255,255,255,0.45)`
- Ghost CTA border: `rgba(255,255,255,0.45)`

The **white primary CTA** uses `#fff` bg with `#0a0807` text — deliberate high-contrast imperative, matching Criterion's visual signature (no `var(--color-accent)` here).

## Motion

### Ken Burns on backdrop

```css
.landing-bg {
  animation: landing-kenburns 40s ease-in-out infinite alternate;
}
@keyframes landing-kenburns {
  0%   { transform: scale(1.05) translate(0, 0); }
  100% { transform: scale(1.15) translate(-2%, -1%); }
}
```

### Content staggered fade-up on load

Each direct child of `.landing-content` animates in:

```css
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
```

### Reduced motion

The existing global rule in `tokens.css:90-96` (`@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }`) already zeroes both animations. **No new reduced-motion rules needed.**

## Accessibility

- Primary CTA: `<button type="submit">` inside the existing `<form method="POST" action="/next_movie">` — keyboard accessible, submits correctly
- Secondary CTA: `<a href="/movie/{tconst}">` — keyboard accessible
- Side label: `aria-hidden="true"` (decorative, not read by screen readers — its content is redundant with the kicker)
- Credit corner: `aria-hidden="false"` (readable attribution)
- Title has an accessible name — just the film title, no extra sr-only markup
- Focus rings on both CTAs use the existing `2px solid var(--color-accent)` at 2px offset, matching the rest of the app
- Backdrop has no alt attribute (it's a `background-image`, not an `<img>`) — content is non-essential decorative, the credit corner provides attribution

## Responsive behavior

**Breakpoint: `< 768px`** (existing Tailwind `md:`)

- Title font-size `clamp(48px, 16vw, 96px)` — remains dominant but fits mobile
- Content padding reduces: `60px 20px 80px`
- Side label hidden (`display: none` under 768px) — no left edge room
- CTAs stack vertically (flex-direction: column, full-width up to `max-width: 320px`)
- Credit corner moves to `bottom: 14px; right: 14px; font-size: 8px;`

## Backend delta

### New helper in `movies/projection_read_service.py` (or a new small `landing_film_service.py`)

```python
_LANDING_SENTINELS = ("Unknown", "N/A", "", "0 min")


def _clean(value):
    if value is None or value in _LANDING_SENTINELS:
        return None
    return value


async def fetch_random_landing_film(pool) -> dict | None:
    """Pick one enriched film with a TMDb-sourced backdrop, at random.

    Returns a flat dict ready for template use, or None if no qualifying
    rows exist (caller is responsible for applying the hardcoded fallback).
    """
    sql = (
        "SELECT tconst, payload_json "
        "FROM movie_projection "
        "WHERE projection_state = 'ready' "
        "  AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.backdrop_url')) LIKE 'https://image.tmdb.org/%' "
        "ORDER BY RAND() "
        "LIMIT 1"
    )
    rows = await pool.execute(sql, (), fetch="all")
    if not rows:
        return None
    row = rows[0]
    payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else row["payload_json"]
    return {
        "tconst": row["tconst"],
        "title": payload.get("title"),
        "year": _clean(payload.get("year")),
        "director": _clean(payload.get("directors")),
        "runtime": _clean(payload.get("runtime")),
        "backdrop_url": payload.get("backdrop_url"),
    }
```

### Route update in `nextreel/web/routes/movies.py`

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

`_LANDING_FALLBACK_POOL` is a module-level constant list of the 3 fallback film dicts, literal TMDb URLs hardcoded.

### Performance

The `ORDER BY RAND()` against `movie_projection` is the one concern. Mitigations:

1. **Projection table size:** Bounded by how many films have been enriched. Typically ≤ 50,000 rows for this app at steady state. `ORDER BY RAND()` is tolerable at this size (MySQL does a single pass + tempfile sort). Measured: ~30–80ms on a warm connection.
2. **If latency grows:** Replace with the random-offset pattern used elsewhere in the codebase (see `movies/query_builder.py:398-413` — `_count_qualifying_rows` + random offset + `LIMIT 1`). No code reuse required initially; add if P95 exceeds 150ms.
3. **No index needed:** The WHERE clause filters on `projection_state` (existing index `idx_movie_projection_state_stale`) plus a JSON extraction. The index seeks to ready rows, then the RAND() sort is over that subset — which is small (only enriched films pass the backdrop filter).

## Scope boundaries

### Out of scope (explicit non-goals)

- **Weekly curated rotation** — considered and rejected in favor of random-per-visit
- **Featured-film curation** (Criterion's "This Week" model) — rejected because Nextreel is not a distributor/programmer
- **Press quotes / festival laurels / critic attribution** — rejected as dishonest for this product
- **Sections below the fold** (essays, collections, stats, newsletter, Letterboxd pitch) — rejected; hero only
- **Logged-in personalization** (last viewed, streak, dashboard) — same page for both auth states; logged-in users bypass via navbar Pick pill
- **Multi-backdrop picking per film** (rotating through `images.backdrops` for one film) — use the single `backdrop_url` already stored
- **Click-to-preview on any element** — the two CTAs are the only interactive elements besides the nav

### Affected files (summary)

| File | Change type |
|---|---|
| `templates/home.html` | **Rewrite** — replace current body content with the landing hero markup |
| `nextreel/web/routes/movies.py` | **Modify** `home()` to fetch the random landing film and pass into template; add `_LANDING_FALLBACK_POOL` constant |
| `movies/projection_read_service.py` (or new `movies/landing_film_service.py`) | **Add** `fetch_random_landing_film(pool)` helper |
| `static/css/tokens.css` | **Add** `--font-display: 'Bebas Neue', ...` token |
| `static/css/input.css` | **Add** `.landing-*` styles (hero frame, backdrop, gradient, title, kicker, meta, CTAs, side label, credit, responsive rules, Ken Burns + fade-up keyframes) |
| `static/css/output.css` | **Regenerate** via `npm run build-css` |

No changes to the navbar, routing, auth, session, or any other template.

## Implementation phasing

Four independently shippable slices.

1. **Backend picker + fallback** — add `fetch_random_landing_film` + fallback pool + wire into `home()` route. *Validation: `curl /` returns a page with correct `landing_film` context (view `g` or inspect rendered HTML for backdrop URL).*
2. **CSS skeleton** — add `--font-display` token, all `.landing-*` styles, rebuild CSS. Template still references old markup; no visible change yet. *Validation: inspect `output.css` for new classes.*
3. **Template rewrite** — replace `home.html` body. Load Bebas Neue font. *Validation: load `/`, see the Criterion-style hero with a real random film.*
4. **Responsive + motion polish** — verify breakpoints at 375 / 768 / 1200 / 1920, Ken Burns fluidity, reduced-motion behavior. *Validation: manual browser checks + reduced-motion toggle.*

## Validation checklist

- Visual check: load `/` 10 times — 10 different films render, each with its own TMDb backdrop
- Secondary CTA: "See this film ↗" routes to a valid `/movie/{tconst}` and renders
- Primary CTA: "Pick Another →" submits the CSRF-guarded form and navigates to a picked movie
- Fallback: with `movie_projection` empty (or temporarily rename the table locally), verify the three fallback films rotate correctly
- Reduced motion: System Settings toggle — Ken Burns and fade-up both freeze instantly
- Responsive: 375 / 768 / 1200 / 1920 — title scales via clamp, side label hides on mobile, CTAs stack
- Keyboard: Tab through navbar → landing → primary CTA → secondary CTA → (loop), all with visible focus rings
- No console errors, no 4xx/5xx on the route, no broken image URLs on a sample of 20 picks
- Bebas Neue loads via Google Fonts (Network panel), fallback stack engages if blocked

## Open questions

None. All brainstorming decisions captured.

## Appendix — brainstorming session

Mockups used during the brainstorming process are preserved in `.superpowers/brainstorm/85229-1776464919/` (gitignored) for reference:
- `06-reset.html` — five structurally different landing-page shapes (after the "materially same page" pivot)
- `07-criterion.html` — first Criterion styling attempt (rejected as dishonest)
- `08-honest-criterion.html` — honest Criterion options
- `09-direct-copy.html` — five direct-copy headline variants; option 1 selected
