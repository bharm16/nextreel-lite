# Movie Filters: Friction Redesign

**Date:** 2026-05-03
**Status:** Design — pending implementation plan
**Owner:** Bryce Harmon

## Context

The movie filter drawer (`templates/_filter_form.html`, opened from the FILTERS tab on the movie detail page) is the primary control surface for narrowing the discovery feed. Today it consists of:

- 6 number inputs (IMDb min/max, Vote Count min/max, Year min/max)
- A 17-option language `<select>` defaulted to English
- 16 vertically stacked genre toggles
- 2 scope toggles (Exclude watched, Hide watchlist) for logged-in users
- Footer: Apply, Reset, Save as default

JavaScript in `static/js/filter-drawer.js` wraps these into 4 collapsible accordion sections (`Ratings & Votes`, `Year & Language`, `Genres`, `Watched`).

## Problem

The form is too high-friction. Specifically, four overlapping pain points (in priority order):

1. **Interaction cost (primary).** Six number inputs for three conceptual ranges plus 16 individual toggles for genres adds up to a heavy tap-and-type burden, especially on mobile.
2. **Cognitive load.** Several controls (notably "Vote Count") expose backend implementation as user choices. Users don't know what to put in.
3. **Blind submission.** Users tweak controls and click Apply with no idea whether they'll get 1,200 results or 12.
4. **State amnesia.** Although `user_navigation_state` already persists active filters between sessions, the persistence is invisible — users don't trust what they can't see, and the existing "Save as default" button is buried in the footer.

Drawer placement (the drawer itself, on the movie detail page) is **not** part of the problem and is not being changed.

## Goals

- Reduce the number of distinct interactions required to express a typical filter intent (e.g., "80s sci-fi rated 7+") by at least 50%.
- Replace number inputs with chip + slider hybrids so common cases are one tap and precise cases remain available.
- Surface live result counts so users know what they'll get before committing.
- Make existing filter persistence legible without changing the persistence model.

## Non-Goals

- No content-preset system ("Date night", "Hidden gems"). Deferred — these address cognitive load (Friction A) which is secondary to interaction cost (Friction B). Revisit after this lands.
- No localStorage persistence for anonymous users. Avoids sync conflicts at sign-up; existing session-scoped nav state is sufficient.
- No "delta count" feedback (`48 fewer matches`). Start with absolute count; layer in delta only if it's free.
- No per-field "Modified" detail. Binary indicator only.
- No change to drawer placement or invocation pattern.
- No change to `MovieCriteria` or the SQL query builder. Chip values resolve to existing ranges server-side.

## Design

### Layout

```
┌─ Filters ─────────────────────────────────×─┐
│  Modified · Reset to my defaults            │  status row (when current ≠ defaults)
├─────────────────────────────────────────────┤
│  ☑ Exclude watched                          │  scope toggles (auth-only)
│  ☐ Hide watchlist                           │
├─────────────────────────────────────────────┤
│  Genres                                      │
│  [All] [Action] [Comedy] [Drama]…           │
│                                              │
│  Year                                        │
│  [Any] [2020s] [2010s] [2000s] [Classic]    │
│  [───●─────────●───] 1995 – 2024            │
│                                              │
│  IMDb Score                                  │
│  [Any] [≥6] [≥7] [≥7.5] [≥8]                │
│  [─────●──●──] 7.0 – 10.0                   │
│                                              │
│  Language                                    │
│  [Any] [English] [Spanish]… More ▾          │
│                                              │
│  Vote Count                                  │
│  [Any] [Mainstream] [Mixed] [Hidden gem]    │
│  [──●─────●──────────] 100k – 200k          │
├─────────────────────────────────────────────┤
│  ~1,240 movies match                        │  sticky footer
│  [   Apply Filters   ]  [Reset]             │
│  Save these as my defaults                  │
└─────────────────────────────────────────────┘
```

Section order rationale:
- **Status row first** — sets state expectation before user touches anything.
- **Scope toggles second** — they're persistent preferences, not per-session filters; pinning them up top lets returning users flip and bail without scrolling.
- **Genres before ranges** — users typically have a genre intent before a precision intent.
- **Year before IMDb** — emotional, low-cognitive-load choice goes first.
- **Vote Count last** — least-used, most-arcane control kept but de-emphasized.

The accordion structure is **removed**. With chip+slider compactness, the entire drawer fits in roughly one viewport scroll, so collapsing sections adds taps for no benefit.

### Component spec

#### Chips (general)

- Visual: rounded pill, ~32px tall on desktop / ~36px on mobile.
- States: default, selected (filled), hover, focus, disabled.
- Multi-select where appropriate (genres); single-select for ranges (selecting `≥7` deselects `≥6`, etc.).
- All chips are `<button type="button">` for native a11y. The form value they represent is held in a hidden `<input>` synced via JS.

#### Genre chip cloud

- 17 chips total: `All` + the existing 16 genres.
- `All` is selected by default and is mutually exclusive with the others. Tapping any specific genre auto-deselects `All`. Selecting all 16 specifics auto-collapses to `All`.
- Layout: flex-wrap, ~4 rows of 4 on desktop, scales narrower on mobile.
- "Select all" / "Clear all" links retained beneath the cloud for power users.
- Submitted form value: `genres[]` array, identical to today's payload.

#### Range chip + slider combo

Used for **Year**, **IMDb Score**, **Vote Count**. Each section contains:

1. A row of preset chips at the top.
2. A dual-handle slider beneath.
3. A small numeric label showing the current min–max selection.

Interaction model:
- Tapping a chip: snaps both slider handles to the chip's range, marks chip selected.
- Dragging either slider handle: deselects all chips, updates numeric label.
- Both controls submit the same underlying form fields (`year_min`, `year_max`, etc.) — chips are a UX shortcut, not a separate data path.

Implementation note for the dual-handle slider: native `<input type="range">` only supports a single handle. The implementation should use a small custom component (two overlaid range inputs with shared track styling, or a tiny dependency-free utility) — no heavyweight slider library. On mobile, **chips are the primary path** and sliders are intentionally secondary because two-finger dragging on a touch slider is fiddly. The design must still be usable mobile-first, with tap-friendly chip targets and the slider treated as a power-user override.

Chip presets:

| Section | Chips | Mapping |
|---|---|---|
| Year | `Any` | `year_min=1900, year_max=current` |
|  | `2020s` | `year_min=2020, year_max=2029` |
|  | `2010s` | `year_min=2010, year_max=2019` |
|  | `2000s` | `year_min=2000, year_max=2009` |
|  | `90s` | `year_min=1990, year_max=1999` |
|  | `Classic` | `year_min=1900, year_max=1989` |
| IMDb Score | `Any` | `imdb_score_min=1.0, imdb_score_max=10.0` |
|  | `≥6` | `imdb_score_min=6.0, imdb_score_max=10.0` |
|  | `≥7` | `imdb_score_min=7.0, imdb_score_max=10.0` |
|  | `≥7.5` | `imdb_score_min=7.5, imdb_score_max=10.0` |
|  | `≥8` | `imdb_score_min=8.0, imdb_score_max=10.0` |
| Vote Count | `Any` | `num_votes_min=0, num_votes_max=2000000` |
|  | `Mainstream` | `num_votes_min=500000, num_votes_max=2000000` |
|  | `Mixed` | `num_votes_min=50000, num_votes_max=499999` |
|  | `Hidden gem` | `num_votes_min=1000, num_votes_max=49999` |

#### Language chips + "More" disclosure

- Top 6 chips inline: `Any`, `English`, `Spanish`, `French`, `Japanese`, `Korean`, `Hindi`.
- `More languages ▾` button reveals the remaining 11 (German, Italian, Portuguese, Russian, Chinese, Arabic, Turkish, Dutch, Swedish, Polish, plus any not in the top 6).
- Single-select (matches current `<select>` semantics).
- **Default changed from `en` to `any`.** This is a deliberate product change — surfaces foreign-language films in default discovery.

#### Scope toggles (Exclude watched, Hide watchlist)

- No control change — keep current toggle UI.
- Move to top of drawer beneath status row.
- Section header dropped; toggles become ambient at the top of the form.

#### Status row

- Visible only when the current form state differs from the comparison baseline (saved defaults if the user has any, system defaults otherwise).
- Binary "Modified" tag, not per-field.
- Reset link label adapts to the user's state:
  - Logged-in user with saved defaults → `Reset to my defaults`
  - Logged-in user without saved defaults, or anonymous user → `Reset to defaults`
- Tapping the reset link snaps all controls back to the comparison baseline.
- Computed client-side by comparing the current form state to a `default_filters` JSON payload rendered into the page on first load.

#### Sticky footer

- Live count badge at the top of the footer, e.g., `~1,240 movies match`.
- During a count fetch, badge shows a subtle loading state (`Counting…` or a spinner).
- Apply / Reset buttons remain in the same positions as today.
- "Save these as my defaults" gets clearer label and a confirmation toast on success.

### Live count

New backend endpoint:

- **Route:** `POST /api/filter_count`
- **Auth:** Same as `/filtered_movie` — accepts session, includes user_id for `exclude_watched` / `exclude_watchlist`.
- **CSRF:** Must accept the same `csrf_token` field as the main filter form. The endpoint is read-only but POST keeps payload-shape parity with `/filtered_movie` and avoids URL-length issues with the genres array. CSRF check stays on for consistency.
- **Payload:** Identical form fields to `/filtered_movie`.
- **Response:** `{"count": int, "cached": bool}`
- **Implementation:** First check `candidate_filter_pool_cache` for the filter signature. If hit, return `len(pool)`. If miss, run a `SELECT COUNT(*) FROM movie_candidates WHERE …` using the existing `MovieQueryBuilder` machinery. Cache the count in Redis at the same key namespace with a short TTL (~60s).
- **Rate limit:** Same bucket as other filter endpoints, with a slightly higher per-minute cap since count requests are cheaper than full filter applications.

Client behavior:
- Debounce 150ms after the last chip tap before firing the request.
- For sliders, fire on `pointerup` (drag end), not continuously.
- Cancel in-flight count requests when a new one supersedes (use `AbortController`).
- Display `~` prefix in the badge (e.g., `~1,240`) to communicate that this is an estimate; round counts ≥1000 to nearest 10.

### State / persistence

No data-model changes. Three pieces of plumbing:

1. **Render `default_filters` into the page** as a JSON island so the client can compute the Modified indicator without a round-trip.
2. **"Reset to my defaults"** loads from the rendered defaults payload (or system defaults for users without saved defaults).
3. **Save toast** — replace the silent server response with a brief in-page toast (`Saved as your defaults`).

Anonymous users:
- Status row shows the same Modified indicator if their current filters differ from system defaults.
- "Save as my defaults" replaced with `Sign in to save your defaults` (link to login, gentle conversion nudge).

### Default values

| Field | Current default | New default |
|---|---|---|
| `year_min` | 1900 | 1900 |
| `year_max` | current year | current year |
| `imdb_score_min` | 7.0 | 1.0 (`Any`) |
| `imdb_score_max` | 10.0 | 10.0 |
| `num_votes_min` | 100000 | 0 (`Any`) |
| `num_votes_max` | 200000 | 2000000 |
| `language` | `en` | `any` |
| `genres_selected` | `[]` (treated as all) | explicit `All` selected |
| `exclude_watched` | on | on (unchanged) |
| `exclude_watchlist` | on | on (unchanged) |

This is a meaningful product shift — the OOTB experience moves from "mainstream English films rated 7+" to "anything goes." Saved defaults from existing users are not migrated; they retain whatever they last saved.

## Backend changes (summary)

- New `POST /api/filter_count` endpoint — counts only, no movie payload.
- Default values in `infra/filter_normalizer.default_filter_state()` updated to match the table in §"Default values" above. The form payload schema is otherwise unchanged.
- Chip presets are a **pure UI concept** — chips update the slider, the slider writes to the existing hidden form inputs (`year_min`, `year_max`, etc.). The server never sees a preset name, so no normalizer changes are needed for presets. This keeps the filter contract minimal.
- `default_filters` rendered into `templates/movie.html` as a `<script type="application/json">` block for client consumption. This is added by extending the existing `inject_csrf_token` app context processor in `nextreel/web/routes/auth.py` so the data flows into every template render with no per-route plumbing. The processor calls `session/user_preferences.get_default_filters` for logged-in users and falls back to `default_filter_state()` for anonymous users.

## Frontend changes (summary)

- `templates/_filter_form.html` — rebuilt around chip + slider components.
- `static/js/filter-drawer.js` — accordion code removed; new modules for chip-state management, slider-chip sync, debounced count fetch, Modified-state diffing.
- New CSS — chip variants, dual-handle slider, status row, sticky footer, count badge.
- Toast component for "Saved as your defaults" confirmation (likely reuses existing toast infra if any; otherwise minimal new utility).

## Migration / rollout

- Default-value change applies only to new sessions. Existing users with saved defaults are unaffected.
- Existing form payload shape is unchanged — chips are a UX layer over the same fields. No breaking change to `/filtered_movie` or `MovieCriteria`.
- The new `/api/filter_count` endpoint is additive; if it fails, the drawer falls back to the legacy "click Apply blind" behavior (graceful degradation).

## Open questions

- **Default change confirmation.** Moving from `imdb≥7, 100k–200k votes, en` to `Any` is a real shift. Owner to confirm this is acceptable.
- **Top 6 language list.** Currently picked by intuition (English, Spanish, French, Japanese, Korean, Hindi). If there's analytics data on actual filter use, consider swapping to data-driven top 6.
- **Vote Count chip thresholds.** The chip-to-range mapping (Mainstream ≥500k, Mixed 50k–500k, Hidden gem 1k–50k) is a guess. May want to validate against the candidate distribution before locking in.
