# Letterboxd CSV Import

## Summary

Allow users to import their watched films from Letterboxd by uploading the `watched.csv` file from their Letterboxd data export. Matched films are added to the user's watched list, which excludes them from movie discovery.

## Context

- Letterboxd's official API is closed-access. The public data export (available at `letterboxd.com/settings/data/`) includes a `watched.csv` with columns: `Date, Name, Year, Letterboxd URI`.
- The CSV does not include IMDb tconst or TMDb IDs. Films must be matched by `(title, year)` against the `movie_candidates` table.
- Prototype testing showed a **94.8% exact match rate** (1,645/1,736). Title normalization (dash variants, casing) is expected to push this to ~97%.

## Data Flow

1. User navigates to `/watched`.
2. Clicks "Import from Letterboxd" to expand the import section.
3. Reads instructions: "Export your data from letterboxd.com/settings/data/, then upload watched.csv here."
4. Uploads `watched.csv` via file input.
5. Server: parses CSV, normalizes titles, bulk-matches against `movie_candidates`, bulk-inserts matched tconsts into `user_watched_movies`.
6. Redirects to `/watched` with flash message: "Imported X films. Y could not be matched."
7. Unmatched films are shown in an expandable list below the flash message.

## Components

### 1. CSV Parser & Matcher — `movies/letterboxd_import.py`

New module with two responsibilities:

**Parsing:**
- `parse_watched_csv(file_stream) -> list[dict]` — reads CSV, returns `[{name: str, year: int}, ...]`.
- Validates that `Name` and `Year` columns exist; raises `ValueError` if missing.
- Skips rows with missing or non-integer year values.

**Title normalization:**
- `normalize_title(title: str) -> str` — applied to both Letterboxd input and DB `primaryTitle` during matching.
- Steps: lowercase, replace en-dash (`\u2013`) and em-dash (`\u2014`) with hyphen (`-`), collapse whitespace.
- Intentionally minimal — avoids stripping punctuation that is meaningful (e.g., colons in "Star Wars: Episode I").

**Matching:**
- `match_films(pool, films: list[dict]) -> MatchResult` — batched query against `movie_candidates`.
- SQL applies the same normalization: `LOWER(REPLACE(REPLACE(primaryTitle, '\u2013', '-'), '\u2014', '-'))`.
- Returns `MatchResult(matched: list[str], unmatched: list[dict], total: int)` where `matched` is a list of tconst strings.

### 2. Bulk Insert — `WatchedStore.add_bulk()`

New method on the existing `WatchedStore` class in `movies/watched_store.py`:
- `async def add_bulk(self, user_id: str, tconsts: list[str]) -> int`
- Multi-value `INSERT INTO user_watched_movies ... ON DUPLICATE KEY UPDATE watched_at = VALUES(watched_at)`.
- Returns count of rows affected (new + updated).
- Invalidates the `watched_tconsts:{user_id}` Redis cache once after the bulk insert.
- Processes in chunks (500 per INSERT) to avoid query size limits.

### 3. Route — `POST /watched/import-letterboxd`

Added to `nextreel/web/routes/watched.py`:
- Protected by `_require_login()` + `@csrf_required`.
- Reads uploaded file via `(await request.files).get("letterboxd_csv")`.
- File size limit: 5MB (checked before parsing).
- Calls `parse_watched_csv()` -> `match_films()` -> `watched_store.add_bulk()`.
- Stores unmatched films in session (key: `letterboxd_unmatched`) for display after redirect.
- Flashes result message and redirects to `/watched`.

### 4. Template — `templates/watched_list.html`

Modify the existing watched list template:
- Add a collapsible "Import from Letterboxd" section above the film list.
- Contains: instructions text, file input (`accept=".csv"`), submit button, hidden CSRF token.
- After import: if `letterboxd_unmatched` is in session, render an expandable "Unmatched films" list below the flash message. Clear from session after display.

## Title Normalization

Applied symmetrically to Letterboxd input and DB values:

| Input | Normalized |
|---|---|
| `Star Wars: Episode I \u2013 The Phantom Menace` | `star wars: episode i - the phantom menace` |
| `GoodFellas` | `goodfellas` |
| `(500) Days of Summer` | `(500) days of summer` |

SQL side: `WHERE LOWER(REPLACE(REPLACE(primaryTitle, '\u2013', '-'), '\u2014', '-')) = %s AND startYear = %s`

## Error Handling

| Condition | Behavior |
|---|---|
| No file uploaded | Flash error: "Please select a CSV file." Redirect to `/watched`. |
| Wrong format (missing Name/Year columns) | Flash error: "Invalid CSV format. Please upload the watched.csv from your Letterboxd export." Redirect. |
| Empty CSV (no data rows) | Flash warning: "The CSV file contained no films." Redirect. |
| All films already in watched list | Flash info: "All X films were already in your watched list." Redirect. |
| DB error during matching or insert | Flash generic error: "Something went wrong during import. Please try again." Log details. |
| File too large (>5MB) | Flash error: "File is too large. Maximum size is 5MB." Redirect. |

## Not Included

- No async worker — synchronous processing is fast enough for CSV sizes typical of personal film lists.
- No progress bar or polling — single request completes in seconds.
- No "replace" mode — import is additive only. Re-importing is idempotent via `ON DUPLICATE KEY`.
- No scraping approach — Cloudflare blocks automated access to Letterboxd profile pages.
- No fuzzy matching beyond title normalization — exact match after normalization is sufficient at ~97%.
- No Letterboxd account linking or OAuth — manual CSV upload only.

## Testing

- Unit tests for `parse_watched_csv()` with valid CSV, malformed CSV, missing columns, empty file.
- Unit tests for `normalize_title()` with dash variants, casing, whitespace.
- Integration test for `match_films()` against a test DB with known titles.
- Unit test for `WatchedStore.add_bulk()` — idempotent insert behavior, cache invalidation.
- Route test for `POST /watched/import-letterboxd` — auth required, CSRF required, happy path, error paths.
