# Public Movie ID — Design Spec

**Date:** 2026-04-26
**Status:** Approved (brainstorming)
**Author:** Bryce + Claude (brainstorming session)

## Summary

Replace the IMDb identifier (`tconst`, e.g. `tt0393109`) in every public-facing
URL with an internal opaque public ID (e.g. `a8fk3j`). Movie detail URLs become
Reddit/Medium-style: `/movie/the-departed-2006-a8fk3j`. The trailing 6-char
opaque ID is the canonical key the route resolves on; the leading title slug
is decorative and can be regenerated when titles are corrected. The IMDb
identifier remains the internal primary key in the database — this change is
strictly at the HTTP boundary.

## Goals

- Remove `tt…` IMDb identifiers from every public surface (URL bar, form
  actions, AJAX paths, share links).
- Provide clean, SEO-friendly URLs that read naturally
  (`/movie/the-departed-2006-a8fk3j`).
- Keep the database schema and internal data flow untouched: `tconst` remains
  the primary key on `movie_projection`, `movie_candidates`, and the user
  list tables.
- Migrate without breaking active sessions or in-flight navigator state.

## Non-goals

- **Removing the IMDb dataset dependency.** The candidates pool still derives
  from the bulk IMDb data; only the public-facing URL changes.
- **Migrating internal storage off `tconst`.** All FK relationships, navigation
  state, and watched/watchlist tables stay tconst-keyed.
- **Backwards compatibility for old `/movie/tt…` URLs.** Old URLs return 404
  on day one; no 301 redirect, no dual-route window.
- **Public/shareable URLs for non-detail routes containing the title slug.**
  POST endpoints and the AJAX projection-state endpoint use the bare 6-char ID.

## Decisions captured

| # | Decision | Rationale |
|---|---|---|
| 1 | Scope: URL-only cleanup. `tconst` remains the internal PK; `public_id` lives on the projection table only. | URL is the visible artifact; storage decoupling would be a much bigger project for marginal additional benefit. |
| 2 | URL shape: Reddit-style `/movie/<title-slug>-<6-char-id>` | Trailing opaque ID is canonical; title slug is decorative and regeneratable. Decouples slug from identity. |
| 3 | ID format: 6 chars, lowercase alphanumeric `[a-z0-9]{6}` (base36) | Looks clean; ~2.18B combos; collisions handled by retry on insert. |
| 4 | ID generation: random via `secrets.choice` with collision-retry on insert (max 8 attempts) | CSPRNG default; idempotent under contention via `UPDATE … WHERE public_id IS NULL`. |
| 5 | Old `/movie/tt…` URLs: 404 hard break, no redirect | Cleanest legal posture — server has no awareness of IMDb URL space. |
| 6 | Route scope: switch all 5 public routes (`/movie/`, `/watched/{add,remove}/`, `/watchlist/{add,remove}/`, `/api/projection-state/`) | DevTools/inspect leakage is real; carrying two identifier conventions through the codebase forever creates confusion. |
| 7 | Title slug: computed from `primaryTitle` + year on the fly, not stored | One source of truth; auto-corrects when titles change; canonical-redirect on slug mismatch keeps SEO clean. |
| 8 | Non-`/movie/` routes: bare 6-char ID, no slug | Not user-shareable; no SEO benefit; reduces AJAX URL bloat. |
| 9 | No Redis cache layer for public_id ↔ tconst lookups in v1 | Single PK/UK lookup is sub-millisecond; premature optimization. Revisit only on hot-path metrics. |
| 10 | Two-deploy phasing: schema first, backfill, code cutover second | Each phase independently rollback-safe. |

## Architecture

### 1. Schema changes

One new column on `movie_projection`:

```sql
ALTER TABLE movie_projection ADD COLUMN public_id CHAR(6) NULL;
ALTER TABLE movie_projection ADD UNIQUE INDEX uq_movie_projection_public_id (public_id);
-- After backfill completes:
ALTER TABLE movie_projection MODIFY COLUMN public_id CHAR(6) NOT NULL;
```

Integration with `infra/runtime_schema.py`:

- `_ensure_column(pool, "movie_projection", "public_id", "CHAR(6) NULL")`.
- `_ensure_index(pool, "movie_projection", "uq_movie_projection_public_id", <create SQL>)`.
- New helper `ensure_movie_projection_public_id_backfill(db_pool)` that mirrors
  `ensure_movie_candidates_shuffle_key`: backfills NULL rows, runs `ALTER
  MODIFY COLUMN ... NOT NULL`, then writes `public_id_backfill_done = '1'` to
  `runtime_metadata` so it never runs again.
- Add the new helpers to `_RUNTIME_REPAIR_HELPER_NAMES`.

No changes to `movie_candidates`, `user_watched_movies`, `user_watchlist`, or
`user_navigation_state`. `tconst` is now an internal-only foreign key.

### 2. Public ID module — `movies/public_id.py`

Constants:

```python
_ID_LENGTH = 6
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 chars
_ID_RE = re.compile(r"^[a-z0-9]{6}$")
_MAX_GENERATION_ATTEMPTS = 8
```

Functions:

- `generate() -> str` — returns 6 chars from `_ID_ALPHABET` via
  `secrets.choice`. Pure.
- `assign_public_id(pool, tconst) -> str` — idempotent. Reads existing
  `public_id` first; if present, returns it. Else attempts
  `UPDATE movie_projection SET public_id = %s WHERE tconst = %s AND public_id
  IS NULL`. On MySQL errno 1062, regenerates and retries up to
  `_MAX_GENERATION_ATTEMPTS`. Raises `PublicIdGenerationError` after the
  retry budget is exhausted.
- `resolve_to_tconst(pool, public_id) -> str | None` — validates format
  via `_ID_RE` (short-circuits malicious slugs before hitting DB), then
  `SELECT tconst FROM movie_projection WHERE public_id = %s LIMIT 1`. Returns
  `None` for both invalid format and not-found.
- `public_id_for_tconst(pool, tconst) -> str | None` — reverse lookup:
  `SELECT public_id FROM movie_projection WHERE tconst = %s LIMIT 1`. Used by
  outbound URL builders that have a tconst in hand (navigator state,
  watched-list rows).

Custom exception: `PublicIdGenerationError(Exception)`.

### 3. URL building — `movies/movie_url.py`

Pure helpers, no DB, no async coupling.

```python
def title_slug(primary_title: str | None, year: str | int | None) -> str:
    """'The Departed' + 2006 → 'the-departed-2006'."""
```

Algorithm:

1. `unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()`
   to strip diacritics (e.g. `Amélie` → `Amelie`).
2. Lowercase.
3. Regex-replace `[^a-z0-9]+` → `-`.
4. Strip leading/trailing hyphens.
5. Truncate slug body to 80 chars (re-strip trailing hyphen after cut).
6. Append `-<year>` if year is parseable as a 4-digit number.
7. Empty/falsy title → `untitled` (defensive).

```python
def build_movie_path(primary_title, year, public_id) -> str:
    return f"/movie/{title_slug(primary_title, year)}-{public_id}"

_PATH_RE = re.compile(r"^(?P<slug>[a-z0-9-]+)-(?P<public_id>[a-z0-9]{6})$")

def parse_movie_path(slug_with_id: str) -> tuple[str, str] | None:
    """Returns (slug_prefix, public_id) or None if malformed."""
```

Canonical-redirect comparison happens in the route handler (not the helper):
if requested slug ≠ recomputed canonical slug, return 301 to canonical URL.

No third-party `python-slugify` dependency — algorithm is ~15 lines of stdlib.

### 4. Route changes & call sites

**Route path patterns:**

| Old | New | File |
|---|---|---|
| `GET /movie/<tconst>` | `GET /movie/<slug_with_id>` | `nextreel/web/routes/movies.py` |
| `POST /watched/add/<tconst>` | `POST /watched/add/<public_id>` | `nextreel/web/routes/watched.py` |
| `POST /watched/remove/<tconst>` | `POST /watched/remove/<public_id>` | `nextreel/web/routes/watched.py` |
| `POST /watchlist/add/<tconst>` | `POST /watchlist/add/<public_id>` | `nextreel/web/routes/watchlist.py` |
| `POST /watchlist/remove/<tconst>` | `POST /watchlist/remove/<public_id>` | `nextreel/web/routes/watchlist.py` |
| `GET /api/projection-state/<tconst>` | `GET /api/projection-state/<public_id>` | `nextreel/web/routes/search.py` |

**Handler shape (every route):**

```python
@bp.route("/watched/add/<public_id>", methods=["POST"])
async def add_to_watched(public_id):
    tconst = await _resolve_public_id_or_404(public_id)
    # ... rest unchanged, still uses tconst internally
```

Top-of-function translation: validate the public_id format, resolve to
tconst, then proceed with existing tconst-based logic. Existing handler
bodies barely change.

**Shared helper in `routes/shared.py`:**

```python
async def _resolve_public_id_or_404(public_id: str) -> str:
    """Returns tconst or aborts 404."""
```

Replaces 6 copies of the validate-then-resolve pattern.

**`/movie/<slug_with_id>` handler also performs canonical-redirect:**

```python
parsed = parse_movie_path(slug_with_id)
if parsed is None: abort(404)
requested_slug, public_id = parsed
tconst = await resolve_to_tconst(pool, public_id)
if tconst is None: abort(404)
movie = await load_projection(...)
canonical_slug = title_slug(movie["primaryTitle"], movie.get("year"))
if requested_slug != canonical_slug:
    return redirect(build_movie_path(...), code=301)
# render
```

**Code that generates URLs:**

- `nextreel/web/routes/navigation.py` — `redirect(url_for(...))` calls switch
  to `redirect(build_movie_path(...))` with a projection lookup.
- `nextreel/web/routes/shared.py` — same pattern.
- Templates (`movie_card.html`, `_watchlist_card.html`, `_watched_card.html`,
  `home.html`) switch to a Jinja global `movie_url(movie)` registered in
  `nextreel/web/app.py`.

**Pre-population: every movie dict handed to a template must carry
`public_id` and `primaryTitle`.** Concretely:

- `route_services._build_view_model` — already loads from `movie_projection`;
  adds `public_id` to the SELECT.
- `movie_navigator` stack entries — currently store `{tconst, ...}`; add
  `public_id` so navigator-rendered URLs don't require an extra DB read per
  render.
- `watched_store` and `WatchlistStore` list views — JOIN against
  `movie_projection` to pull `public_id` alongside the existing tconst rows.
- `landing_film_service` — include `public_id` in the SELECT for the home
  landing-film card.

**`_TCONST_RE` retirement:** currently a path validator in `shared.py`,
`movies.py`, `watched.py`, `search.py`. Each call site replaces it with the
public_id flow. The constant moves to `movies/public_id.py` (or is deleted
entirely if nothing else uses it).

**Login `next=` parameter:** `templates/movie_card.html` builds
`next=/movie/<tconst>` paths. Switches to `next={{ movie_url(movie) }}`. No
change required to `_safe_next_path` in `nextreel/web/routes/auth.py` — it
is shape-only (must start with `/`, no `//` open-redirect, no control
chars), and the new URL shape passes that check unchanged.

### 5. Worker enrichment hook

The `enrich_projection` arq job inserts new projection rows. After the INSERT
in `projection_repository`'s upsert path, call `assign_public_id(pool,
tconst)`. Failure to assign is non-fatal (logged + retried by the next
enrichment); the column being NULL transiently is tolerable until the next
run.

### 6. Backfill & rollout phasing

**Phase 1 — Schema-only deploy.** Adds the column and unique index; ships
no route changes. Production keeps serving `/movie/tt…` URLs, column sits
empty.

**Phase 2 — Backfill (idempotent).** Gated by `runtime_metadata` row
`public_id_backfill_done`. Runs at app startup if the row is absent:

1. `SELECT tconst FROM movie_projection WHERE public_id IS NULL ORDER BY
   tconst`.
2. For each, call `assign_public_id` (the
   `UPDATE ... WHERE public_id IS NULL` clause + 1062 retry handles
   concurrent enrichment writes).
3. Process in chunks of 1000; commit per chunk.
4. After loop: `ALTER TABLE movie_projection MODIFY COLUMN public_id CHAR(6)
   NOT NULL`.
5. Insert `public_id_backfill_done = '1'` into `runtime_metadata`.

If interrupted (process killed mid-loop), startup re-enters the same loop
and resumes from rows that remain NULL. No manual recovery needed.

**Phase 3 — Code cutover deploy.** Lands all route changes, URL helpers,
template updates, and the `movie_url()` Jinja global. After this deploy:

- `/movie/tt0393109` returns 404 (route shape no longer matches).
- All generated links use `/movie/the-departed-2006-a8fk3j`.
- POST endpoints accept the new public_id.

**Pre-deploy assertion (deploy 2 startup):** before swapping routes, assert
zero `movie_projection` rows have `public_id IS NULL`. If any exist, refuse
to start with a clear error rather than serving broken links.

**Cached navigator stack entries from before deploy 2:** if a user's session
contains stack entries that pre-date the deploy and don't carry a `public_id`
field, the URL builder falls back to a `public_id_for_tconst` lookup. One-off
cost during the rollover; sessions cycle out within
`MAX_SESSION_DURATION_HOURS` (8h default).

**Lookout metric:** add a counter `tt_url_404_total` to the 404 handler so
we can observe how many bookmarks/external links still hit the dead URL
pattern post-cutover.

**Rollback posture:** if deploy 2 has issues, revert the deploy. Schema
(deploy 1) and backfilled data both stay in place — additive and harmless to
the old code.

## Testing

**Unit — `movies/public_id.py`:**

- `generate()` produces 6 chars from `[a-z0-9]`, varies across calls.
- `_ID_RE` accepts valid IDs, rejects `tt0393109`, uppercase, wrong length,
  special chars.
- `assign_public_id` is idempotent: two calls on the same tconst return
  the same ID.
- `assign_public_id` retries on simulated 1062 collision (mock raises once,
  succeeds on retry).
- `assign_public_id` raises `PublicIdGenerationError` after
  `_MAX_GENERATION_ATTEMPTS` simulated collisions.
- `resolve_to_tconst("not-a-real-id")` returns `None` without DB hit.
- `resolve_to_tconst(<unknown>)` returns `None` (DB returns no row).

**Unit — `movies/movie_url.py`:**

- `title_slug("The Departed", 2006)` → `"the-departed-2006"`.
- `title_slug("Amélie", 2001)` → `"amelie-2001"`.
- `title_slug("Star Wars: Episode IV — A New Hope", 1977)` →
  `"star-wars-episode-iv-a-new-hope-1977"`.
- `title_slug("3:10 to Yuma", 2007)` → `"3-10-to-yuma-2007"`.
- `title_slug("", 2006)` → `"untitled-2006"`.
- `title_slug("Title", None)` → `"title"` (no trailing year).
- 80-char truncation: very long title, no trailing hyphen.
- `parse_movie_path("the-departed-2006-a8fk3j")` →
  `("the-departed-2006", "a8fk3j")`.
- `parse_movie_path("nonsense")` → `None`.
- `build_movie_path("The Departed", 2006, "a8fk3j")` →
  `"/movie/the-departed-2006-a8fk3j"`.

**Integration (real DB pool):**

- `GET /movie/the-departed-2006-<real_id>` returns 200, renders movie page.
- `GET /movie/<wrong-slug>-<real_id>` returns 301 to canonical, 200 on
  follow.
- `GET /movie/anything-aaaaaa` (well-formed but unknown ID) returns 404.
- `GET /movie/tt0393109` returns 404.
- `POST /watched/add/<real_id>` succeeds, inserts the right tconst into
  `user_watched_movies`.
- `POST /watched/add/zzzzzz` (unknown ID) returns 404.
- `GET /api/projection-state/<real_id>` returns the expected JSON.
- Login: `next=/movie/the-departed-2006-a8fk3j` accepted by allowlist;
  `next=/movie/tt0393109` rejected.

**Backfill:**

- Fresh test DB with `movie_projection` rows that have NULL `public_id`.
- Run `ensure_movie_projection_public_id_backfill`.
- Assert: every row has a 6-char `public_id`, all unique, column is
  `NOT NULL`, and `runtime_metadata.public_id_backfill_done = '1'`.
- Run again; assert it short-circuits.

**Template-coverage guard:**

A test that exercises every code path that produces a movie dict for a
template (`route_services._build_view_model`, `movie_navigator` push,
watched-list query, watchlist-list query, home landing-film,
`letterboxd_import_service`) and asserts the resulting dicts include both
`public_id` and `primaryTitle`. Catches the "broken link with no error"
failure mode early.

**Concurrency:**

Two coroutines concurrently call `assign_public_id` for the same NULL
tconst against a real MySQL pool (not mocked). Assert both return the same
ID (idempotency under contention).

## Out of scope (explicit non-tests)

- Slug stability across title corrections — covered by canonical-redirect
  integration test.
- Performance benchmarking — all SELECTs are PK/UK lookups.
- Old `tt…` route behavior beyond the integration "404 on tt0393109" check.
