# Watchlist — Design Spec

**Date:** 2026-04-25
**Status:** Approved (brainstorming)
**Author:** Bryce + Claude (brainstorming session)

## Summary

Add a per-user **Watchlist** ("save for later") feature, mirroring the existing
Watched-list pattern. Users can toggle a movie onto or off their watchlist from
the movie detail page; saved movies appear on a dedicated `/watchlist` page
with the same filtering, sorting, and pagination affordances as `/watched`.
Watchlist movies are excluded from the discovery queue by default, controllable
via a new account-page toggle.

## Goals

- Provide a persistent, server-side bookmark list of movies the user wants to watch.
- Make the toggle one click from the movie detail page.
- Reuse the watched-list UX patterns (filter chips, sort, pagination, AJAX) so
  users get a consistent experience.
- Avoid touching the working watched code beyond the navigator's exclusion path.

## Non-goals (v1)

- **Letterboxd watchlist CSV import.** Deferred — easy to bolt on later by
  pointing the existing import pipeline at `WatchlistStore`.
- **Navbar count badge.** Deferred — Watched doesn't show one today.
- **Bulk operations** (move all to watched, clear list).
- **Public/shared watchlists.**
- **Recommendations driven by watchlist contents.**
- **Auto-removal when a watchlist movie is marked watched.** The two lists
  coexist independently — explicitly chosen during brainstorming.

## Decisions captured

| # | Decision | Rationale |
|---|---|---|
| 1 | Name: **Watchlist** (table `user_watchlist`, route `/watchlist`, class `WatchlistStore`, button "Add to watchlist") | Conventional in movie apps (Letterboxd, IMDb, JustWatch); implies queueing intent. |
| 2 | Watchlist coexists with Watched independently — no auto-removal on mark-watched | Simplest implementation; no cross-table transactions; user controls cleanup. |
| 3 | New user setting `exclude_watchlist_default = TRUE` (with account-page toggle); excluded from discovery by default | Mirrors `exclude_watched_default`; principle of least surprise. |
| 4 | Full-parity list page (filter chips, 5 sort options, pagination, AJAX progressive load) — default sort `added_at DESC` | Watched-page patterns are mature; copy-with-rename is cheap. |
| 5 | Movie-page button placement: 4-button bottom nav `[Prev][Watchlist][Watched][Next]` | Same affordance level as Watched; mobile collapses with the existing prefix-hide trick. |
| 6 | Approach: **parallel sibling** (copy watched code, rename) — no shared abstraction yet | Watched code wasn't built generic; abstracting now is risk for speculative future benefit. |

## Architecture

### Data model

#### New table `user_watchlist`

```sql
CREATE TABLE user_watchlist (
    user_id  CHAR(32) NOT NULL,
    tconst   VARCHAR(16) NOT NULL,
    added_at DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id, tconst),
    KEY idx_watchlist_user_added (user_id, added_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

Added to `_RUNTIME_SCHEMA_TABLE_DEFINITIONS` in `infra/runtime_schema.py` so
it's `CREATE TABLE IF NOT EXISTS`-managed at boot.

#### New column on `users`

```sql
ALTER TABLE users ADD COLUMN exclude_watchlist_default BOOLEAN NOT NULL DEFAULT TRUE
```

Added via a new `_ensure_column` repair helper (idempotent, catches errno 1060).
Helper name appended to `_RUNTIME_REPAIR_HELPER_NAMES`.

### Data access — `movies/watchlist_store.py`

Class `WatchlistStore`. Mirrors `movies/watched_store.py`. Public surface:

- `add(user_id, tconst)` — `INSERT … ON DUPLICATE KEY UPDATE added_at`
- `remove(user_id, tconst)` — `DELETE`
- `is_in_watchlist(user_id, tconst) -> bool`
- `watchlist_tconsts(user_id) -> set[str]` — Redis-cached at
  `user:watchlist_tconsts:{user_id}`, 5-min TTL, invalidated on add/remove
  (uses `safe_get_or_set`)
- `count(user_id) -> int`
- `list_watchlist_filtered(user_id, *, sort, limit, offset, decades, rating_min, rating_max, genres) -> list[dict]`
- `count_filtered(user_id, **filters) -> int`
- `available_filter_chips(user_id) -> {decades, genres, ratings}`

Sort whitelist (mirrors watched, default `recent`):

```python
{
    "recent":      "w.added_at DESC",
    "title_asc":   "c.primaryTitle ASC",
    "title_desc":  "c.primaryTitle DESC",
    "year_desc":   "c.startYear DESC, c.primaryTitle ASC",
    "rating_desc": "c.averageRating DESC, c.primaryTitle ASC",
}
```

`add_bulk`, `ready_tconsts_for_import`, and `ready_import_rows` are **not**
implemented in v1 (Letterboxd-import-only methods).

Wired into `nextreel/bootstrap/movie_manager_factory.py` and exposed as
`MovieManager.watchlist_store`. Cache attached in `MovieManager.attach_cache`.

### Routes — `nextreel/web/routes/watchlist.py`

| Route | Method | Auth | CSRF | Rate-limit bucket |
|---|---|---|---|---|
| `/watchlist` | GET | required (302 to login) | — | — |
| `/watchlist/add/<tconst>` | POST | required (401) | yes | `"watchlist"` |
| `/watchlist/remove/<tconst>` | POST | required (401) | yes | `"watchlist"` |

- `<tconst>` validated against the shared `_TCONST_RE`.
- Toggle endpoints support both HTML form-post (303 to safe referrer) and
  JSON (`Accept: application/json` →
  `{"ok": true, "is_in_watchlist": bool, "tconst": "..."}`).
- Rate-limit decorator: `@rate_limited("watchlist")` — same `RATE_LIMIT_MAX`
  defaults as `"watched"` to start.
- Pagination/sort/filter helpers copied with renames (`_parse_watchlist_pagination`,
  `_VALID_SORTS`).
- Blueprint slice registered in `nextreel/web/routes/__init__.py` alongside
  the existing `from nextreel.web.routes.watched import (...)` import line.

### Presenter — `nextreel/web/route_services.py`

New `WatchlistPresenter` class, copy of `WatchedListPresenter`. Renames inside
`_normalize_row`:

- `watched_at` → `added_at`
- "this month" stat repurposed as **"added this month"**

`MovieDetailService.build()` gains an `is_in_watchlist` field on the returned
`MoviePresentation` (parallel `gather()` call alongside `is_watched`). The
movies route at `nextreel/web/routes/movies.py:93` sets
`g.is_in_watchlist = view_model.is_in_watchlist`.

### Discovery exclusion — `nextreel/application/movie_navigator.py`

`MovieNavigator.__init__` gains a `watchlist_store=None` parameter. Composition
root `nextreel/bootstrap/movie_manager_factory.py` wires it.

New parallel method (mirrors `_watched_exclusion_set`):

```python
async def _watchlist_exclusion_set(self, state) -> set[str]:
    if (
        not self.watchlist_store
        or not getattr(state, "user_id", None)
        or not state.filters.get("exclude_watchlist", True)
    ):
        return set()
    return set(await self.watchlist_store.watchlist_tconsts(state.user_id))
```

Two call sites change. In `_refill_queue` and `_pop_next_queue_ref`:

```python
watched_exclusion   = await self._watched_exclusion_set(state)
watchlist_exclusion = await self._watchlist_exclusion_set(state)
excluded |= watched_exclusion | watchlist_exclusion
```

The optional `watched_exclusion` cache parameter on those methods grows a
sibling `watchlist_exclusion` parameter so outer callers can compute both once
and reuse. Both default to `None` (lazy fetch — preserves existing call shape).

**Cache impact:** `candidate_store._store_filter_pool` already skips caching
when `excluded_tconsts` is non-empty. Adding watchlist tconsts to the
exclusion set doesn't introduce new cache pollution — it's the same uncached
path watched users already take.

### Navigation state — `nextreel/application/navigation_state_service.py`

`NavigationState.filters` already supports arbitrary keys. Add a new standard
key `exclude_watchlist: bool`, populated from `users.exclude_watchlist_default`
at the same site `exclude_watched` is loaded.

### Filter form — `templates/_filter_form.html`

The inline filter form on the movie detail page gains a checkbox for
`exclude_watchlist` mirroring `exclude_watched`. Reusing the existing
`/filtered_movie` flow means **no new route** — just a parser bump in
`infra/filter_normalizer.py` to recognize `exclude_watchlist=on`.

### Templates

| File | Source | Notes |
|---|---|---|
| `templates/watchlist.html` | copy of `templates/watched_list.html` | Filter chips + sort dropdown + pagination + AJAX progressive load identical. **Empty-state copy replaced** — see below. No Letterboxd-import block. |
| `templates/_watchlist_card.html` | copy of `templates/_watched_card.html` | Shows "Added Mar 14" in place of "Watched Mar 14". |

**Empty state for `/watchlist`:**

> ### Your watchlist is empty
> Movies you save while browsing show up here. Start discovering, then click
> *Add to watchlist* on any movie page.
>
> [ Discover movies → ]  *(links to home)*

### Movie-page bottom nav — `templates/movie_card.html`

The sticky `<nav class="movie-nav-bar">` becomes a 4-button row:

```
[← Previous]  [Watchlist]  [Watched]  [Next →]
```

The watchlist toggle is a near-clone of the existing watched form block, with
parallel data attributes (`data-watchlist-toggle-form`,
`data-watchlist-state`, `data-add-url`, `data-remove-url`).

Logged-out fallback: a styled `<a>` linking to
`login_page?next=/movie/{tconst}`, identical pattern to the watched fallback.

### CSS / JS

- New `nav-btn-watchlist` styles, sibling to `nav-btn-watched`. Bookmark/star
  outline icon. Active state uses the existing accent treatment.
- Mobile collapse: `.nav-btn-watchlist__prefix { display: none }` below the
  current breakpoint, matching the watched button's responsive behavior.
- New file `static/js/watchlist-toggle.js` — copy of the AJAX toggle in
  `static/js/movie-card.js:60-115`, scoped to the new data attributes. Loaded
  with `?v={{ config.get('CSS_VERSION', '1') }}` cache-busting.

### Account page — `templates/account.html`

Add a parallel toggle row mirroring `exclude_watched_default`: same Tailwind
classes, same `aria-checked` pattern, hidden input named
`exclude_watchlist_default`. The save handler in the account route gains a
parallel field read.

No watchlist count is shown on the account page (there is no watched count
there today either — keeping symmetry; counts live on the list pages).

### Navbar — `templates/navbar_modern.html`

Add `<a href="{{ url_for('main.watchlist_page') }}">Watchlist</a>` immediately
after the Watched link in both the desktop and mobile menus. **No count badge**
(mirroring how Watched is shown today).

## Testing

| Test file | What's added |
|---|---|
| `tests/movies/test_watchlist_store.py` *(new)* | add/remove/idempotency; `watchlist_tconsts` cache hit/miss/invalidation; `list_watchlist_filtered` sort + filter SQL coverage; `count_filtered` |
| `tests/web/test_watchlist_routes.py` *(new)* | auth-required redirects, CSRF, JSON vs HTML response shapes, rate-limit decoration, pagination param parsing |
| `tests/web/test_route_services.py` *(extend)* | `WatchlistPresenter.build()` shape; `MovieDetailService` returns `is_in_watchlist` |
| `tests/web/test_routes_navigation.py` *(extend)* | navigator excludes watchlist tconsts when `state.filters["exclude_watchlist"]` is True; combines correctly with watched exclusion |
| `tests/web/test_account_routes.py` *(extend)* | toggling `exclude_watchlist_default` persists to DB and surfaces in next session bind |
| `tests/infra/test_runtime_schema.py` *(extend)* | new `user_watchlist` table created on boot; `exclude_watchlist_default` column ensured on `users` |
| `tests/infra/test_filter_normalizer.py` *(extend)* | `exclude_watchlist=on` parses to `True` in filter dict |

Coverage gate (40% on Py 3.11 / 3.12) — comfortably exceeded by parallel-mirror
ratios on already-covered code.

## File-by-file change summary

**New files**

- `movies/watchlist_store.py`
- `nextreel/web/routes/watchlist.py`
- `templates/watchlist.html`
- `templates/_watchlist_card.html`
- `static/js/watchlist-toggle.js`
- `tests/movies/test_watchlist_store.py`
- `tests/web/test_watchlist_routes.py`

**Modified files**

- `infra/runtime_schema.py` — add `user_watchlist` table; add
  `_ensure_column` helper for `users.exclude_watchlist_default`; append helper
  name to `_RUNTIME_REPAIR_HELPER_NAMES`.
- `nextreel/bootstrap/movie_manager_factory.py` — instantiate `WatchlistStore`,
  pass to `MovieManager` and `MovieNavigator`.
- `nextreel/application/movie_service.py` — accept and expose `watchlist_store`;
  attach cache.
- `nextreel/application/movie_navigator.py` — accept `watchlist_store`; new
  `_watchlist_exclusion_set`; union into both call sites.
- `nextreel/application/navigation_state_service.py` — load
  `exclude_watchlist` filter from `users.exclude_watchlist_default`.
- `nextreel/web/route_services.py` — new `WatchlistPresenter` and view-model;
  `MovieDetailService` parallel `is_in_watchlist` gather.
- `nextreel/web/routes/shared.py` — register `_watchlist_list_presenter`;
  re-export.
- `nextreel/web/routes/movies.py` — set `g.is_in_watchlist`.
- `nextreel/web/routes/__init__.py` — import `watchlist` route handlers
  alongside the existing `watched` import.
- `nextreel/web/routes/account.py` — `account_preferences_save` reads new
  `exclude_watchlist_default` form field and persists to the `users` row,
  mirroring the existing `exclude_watched_default` handling.
- `infra/filter_normalizer.py` — parse `exclude_watchlist=on`.
- `templates/movie_card.html` — 4th nav button + logged-out fallback.
- `templates/_filter_form.html` — `exclude_watchlist` checkbox.
- `templates/account.html` — `exclude_watchlist_default` toggle + saved count.
- `templates/navbar_modern.html` — add "Watchlist" link in both menus.
- `tests/web/test_route_services.py`, `tests/web/test_routes_navigation.py`,
  `tests/web/test_account_routes.py`, `tests/infra/test_runtime_schema.py`,
  `tests/infra/test_filter_normalizer.py` — extensions per testing table above.

## Open questions

None — all decisions captured during brainstorming.

## Risks

- **Navigator union of two exclusion sets at every `_pop_next_queue_ref` loop
  iteration.** Both sets are typically small (<100 entries) and Python set
  union is cheap; negligible. Spec is to keep the sets fetched once per call
  via the optional cache parameter.
- **Filter-pool cache bypass.** Adding watchlist exclusion broadens the case
  where `_store_filter_pool` skips caching. Same behavior watched users
  already trigger today — no regression, just slightly more cache misses for
  watchlist-using accounts.
- **Account-page UI density.** Adding a second exclude-default toggle plus a
  saved-count stat next to the existing watched controls. Reviewer should
  confirm visual hierarchy still works (will be visible in implementation
  preview).
