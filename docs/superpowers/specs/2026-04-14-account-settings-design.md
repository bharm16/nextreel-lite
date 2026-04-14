# Account Settings — Design Spec

**Date:** 2026-04-14
**Status:** Design approved, ready for implementation plan
**Scope:** v1 — Profile, Security, Preferences, Data, Danger zone
**Explicitly deferred (phase 2):** Notifications (email pipeline), Integrations (Letterboxd/Trakt OAuth, personal API tokens), email change flow

## 1. Goal

Add a single, authenticated account settings surface at `/account` that lets users:

- Edit their profile (display name, see email, see account age)
- Manage password (email users) and active sessions
- Control viewing preferences (watched-exclusion, theme, default filter presets)
- Import/export their watched data (Letterboxd CSV in, CSV + JSON out)
- Clear watched history or delete their account

The design reuses existing infrastructure (blueprints, CSRF, rate limiting, session store, arq worker, `users` table, `letterboxd_import_service`) and introduces the minimum new surface area consistent with the v1 scope.

## 2. Architecture

### 2.1 Routing

All account routes live in a new blueprint `nextreel/web/routes/account.py` registered in `nextreel/web/app.py`. A single `@bp.before_request` hook redirects unauthenticated visitors to `/login?next=<original-url>`.

| Route | Method | Purpose |
|---|---|---|
| `/account` | GET | Redirect to `/account?tab=profile` |
| `/account?tab=<name>` | GET | Render tab shell + requested panel (`profile`, `security`, `preferences`, `data`, `danger`) |
| `/account/profile` | POST | Save display name |
| `/account/password` | POST | Change password (email users); revokes other sessions on success |
| `/account/sessions/revoke` | POST | Sign out of all other sessions |
| `/account/preferences` | POST | Save `exclude_watched_default` and `theme_preference` |
| `/account/preferences/filters/save` | POST | Save the current filter form as defaults (invoked from `/filters`) |
| `/account/preferences/filters/clear` | POST | Clear saved filter defaults |
| `/account/import/letterboxd` | POST | Accept CSV upload, enqueue arq job, redirect to progress page |
| `/account/import/<import_id>` | GET | Progress page (polls the status endpoint) |
| `/account/import/<import_id>/status` | GET (JSON) | Poll endpoint for progress; scoped by `user_id` |
| `/account/export/watched.csv` | GET | Stream watched list as CSV |
| `/account/export/watched.json` | GET | Stream watched list as JSON |
| `/account/watched/clear` | POST | Delete all rows from `user_watched_movies` for this user |
| `/account/delete` | POST | Delete account (transactional cascade), revoke sessions, logout, redirect `/` |

### 2.2 Navbar entry point

`templates/navbar_modern.html` updated so that **when logged in**:

- The "Log Out" link is replaced by a small **initials circle** (the `account-avatar` pattern — see §6).
- Clicking the circle opens a dropdown with: **Account**, **Watched**, **Log out**.
- On mobile, the mobile slide-down panel gains an **Account** link.

Logged-out state is unchanged.

### 2.3 Tab structure

Five tabs, rendered as a top tab bar on `/account`:

1. Profile
2. Security
3. Preferences
4. Data
5. Danger zone (red accent)

`?tab=<name>` selects the panel. Shareable URLs. One route handler per POST action — panels are distinct templates under `templates/account/`.

## 3. Schema changes

Applied idempotently via `infra/runtime_schema.py` `_ensure_column` / `_ensure_index` helpers (swallow MySQL 1060/1061 errnos).

### 3.1 New columns on `users`

```sql
ALTER TABLE users ADD COLUMN default_filters_json JSON NULL;
ALTER TABLE users ADD COLUMN theme_preference VARCHAR(10) NULL;
-- theme_preference values: 'light' | 'dark' | 'system' | NULL (unset; follow device)
```

### 3.2 New table `letterboxd_imports`

```sql
CREATE TABLE IF NOT EXISTS letterboxd_imports (
    import_id     CHAR(32) PRIMARY KEY,
    user_id       CHAR(32) NOT NULL,
    status        VARCHAR(16) NOT NULL,   -- 'pending'|'running'|'completed'|'failed'
    total_rows    INT NULL,
    processed     INT NOT NULL DEFAULT 0,
    matched       INT NOT NULL DEFAULT 0,
    skipped       INT NOT NULL DEFAULT 0,
    failed        INT NOT NULL DEFAULT 0,
    error_message TEXT NULL,
    created_at    DATETIME(6) NOT NULL,
    updated_at    DATETIME(6) NOT NULL,
    completed_at  DATETIME(6) NULL,
    KEY idx_letterboxd_user_created (user_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.3 Delete-account cascade

No ON DELETE CASCADE on existing FKs. `/account/delete` runs this ordered transaction:

```sql
DELETE FROM user_watched_movies    WHERE user_id = %s;
DELETE FROM user_navigation_state  WHERE user_id = %s;
DELETE FROM letterboxd_imports     WHERE user_id = %s;
DELETE FROM users                  WHERE user_id = %s;
```

Plus a Redis sweep of quart-session keys belonging to the user (see §5.3).

### 3.4 Not added (explicit non-decisions)

- No `user_auth_methods` table (linked providers are read-only — §4.2)
- No `deleted_at` column (hard delete — §4.5)
- No `email_verifications` table (email immutable in v1 — §4.1)
- No avatar storage (initials only — §4.1)

## 4. Per-tab behavior

### 4.1 Profile tab

Single form, POST `/account/profile`.

- **Display name** — text input, 1–100 chars (matches existing `display_name VARCHAR(100)`). Blank means: render as the part of the email before `@`.
- **Avatar preview** — initials circle. 1–2 chars derived from display name (or email local-part if no display name). Background color derived from `user_id` hash → one of ~8 palette colors defined as CSS variables. Rendered by a new Jinja macro `user_avatar(user, size='sm'|'md'|'lg')` in `templates/macros.html`, reused in the navbar dropdown.
- **Email** — rendered as read-only text with a small "Contact support to change" note. Email is immutable in v1.
- **Signed in with** — small badge: "Email", "Google", or "Apple".
- **Member since** — `created_at` formatted as "Month YYYY".

### 4.2 Security tab

**Password card** (rendered only for `auth_provider = 'email'` users)

- Form fields: current password, new password, confirm new password.
- Server flow:
  1. `verify_password_async(current_password, row.password_hash)` → reject if wrong
  2. Validate `len(new_password) >= MIN_PASSWORD_LENGTH` and `new == confirm`
  3. `UPDATE users SET password_hash = %s, updated_at = %s WHERE user_id = %s`
  4. Call `revoke_user_sessions(redis, user_id, except_session_id=current)` (§5.3)
  5. Flash "Password updated. Other devices were signed out." → redirect `/account?tab=security`

OAuth users see a stub card: "Your password is managed by your <Provider> account. Visit <provider>.com to change it."

**Linked providers card** — read-only row showing `auth_provider`. No actions.

**Sessions card** — one button "Sign out of all other sessions". POSTs `/account/sessions/revoke`, which calls the same `revoke_user_sessions` primitive.

### 4.3 Preferences tab

Single form, POST `/account/preferences`.

- **Hide movies I've already watched** — toggle bound to `users.exclude_watched_default`. Reuses the existing getter/setter in `session/user_preferences.py`.
- **Theme** — radio: Light / Dark / Use system setting. Writes `theme_preference`. The page shell reads this column at render time and sets `data-theme-server` on `<html>`. Client-side JS still allows per-device override via `localStorage.nr-theme` (existing toggle retained), but the server-set value is the default.
- **Default filters card (read-only)** — shows either "No defaults saved — visit Filters and click **Save as default**" or a human summary of `default_filters_json` (e.g. "Genres: Horror, Thriller · Year ≥ 2000 · Rating ≥ 7.0"), plus a **Clear** button that POSTs to `/account/preferences/filters/clear`.

**Filter-page integration:** `templates/set_filters.html` gains a second button next to Apply labeled **Save as default**. That button POSTs the same form body to `/account/preferences/filters/save`, which reuses `movies/filter_parser.py` to validate and serialize into `default_filters_json`.

### 4.4 Data tab

Three cards.

**Import from Letterboxd**
- `<input type="file" accept=".csv">` + Upload button.
- POST `/account/import/letterboxd` (multipart):
  1. Validate content-type, MIME, and size (cap: **5 MB**)
  2. Generate `import_id = uuid4().hex`
  3. `INSERT INTO letterboxd_imports (import_id, user_id, status='pending', ...)`
  4. `SET` Redis key `letterboxd:import:{import_id}:csv` with the raw CSV body, 24h TTL
  5. `await ctx.enqueue_job('import_letterboxd', import_id)`
  6. Redirect to `/account/import/{import_id}`
- Progress page `templates/account/import_progress.html` polls `/account/import/{import_id}/status` every 2s, shows `processed/total_rows` as a progress bar plus a live "Matched / Skipped / Failed" breakdown. On terminal states (`completed`, `failed`) it stops polling and shows a final summary + a link back to `/account?tab=data`.
- Below the upload card: a **history list** (last 5 imports for this user) with timestamp, status, and counts.

**Export watched list**
- Two links: **Download CSV**, **Download JSON**.
- CSV columns: `Date, Name, Year, Letterboxd URI` (matches Letterboxd export format; URI column empty since we don't store it).
- JSON: `[{tconst, title, year, watched_at, poster_url}, ...]`
- Both endpoints use Quart streaming responses (no full list in memory).
- `Content-Disposition: attachment; filename="nextreel-watched-YYYY-MM-DD.<ext>"`

**Clear watched history**
- Button "Clear all watched history". Opens modal: "This will remove all N movies from your watched list. This cannot be undone. [Cancel] [Clear everything]".
- POST `/account/watched/clear` → `DELETE FROM user_watched_movies WHERE user_id = %s`.
- Flash "Watched list cleared." → redirect `/account?tab=data`.

### 4.5 Danger zone

Single card (red accent, `.account-danger-card`).

- Button "Delete my account permanently".
- Modal: explanatory paragraph (data destroyed, cannot be recovered), then **"Type your email to confirm: you@example.com"** input. Button stays disabled until typed email matches (case-insensitive).
- On submit, POST `/account/delete`:
  1. Re-validate the typed email matches the session user's email (case-insensitive, matching the client-side check)
  2. Run the ordered cascade (§3.3) inside a single transaction
  3. `revoke_user_sessions(redis, user_id, except_session_id=None)` (kills current too)
  4. `session.clear()`
  5. Flash "Your account has been deleted." → redirect `/`

## 5. Background work and shared primitives

### 5.1 Letterboxd import job

New arq function `import_letterboxd(ctx, import_id)` in `nextreel/workers/worker.py`, registered in `WorkerSettings.functions`.

Flow:
1. Load `letterboxd_imports` row; set `status='running'`, stamp `updated_at`
2. Read CSV body from `letterboxd:import:{import_id}:csv`; bail to `failed` if missing
3. Parse via existing `movies/letterboxd_import.py`; count rows → set `total_rows`
4. For each row:
   - Resolve title → tconst via existing matcher
   - On match: `INSERT ... ON DUPLICATE KEY UPDATE` into `user_watched_movies`
   - Increment in-memory counters (`processed`, `matched`/`skipped`/`failed`)
5. Flush counters to the row every 25 rows or 2 seconds (whichever first) so the poll endpoint sees movement
6. On success: `status='completed'`, `completed_at=now()`, flush final counters, `DEL` the Redis CSV blob
7. On exception: `status='failed'`, `error_message=str(exc)[:500]`, re-raise (arq retry policy applies). After final retry the row stays `failed`; the Redis blob GC's via 24h TTL.

Starts on the default `WorkerSettings` queue. If enrichment throughput suffers, move to `MaintenanceWorkerSettings` (one-line change).

### 5.2 Exports are synchronous-streamed

Watched lists are per-user and bounded (<10k rows typical). Quart streaming responses yield rows directly; no background job, no temp files.

### 5.3 Session revocation primitive

New module `session/revocation.py`:

```python
async def revoke_user_sessions(
    redis_client,
    user_id: str,
    *,
    except_session_id: str | None = None,
) -> int:
    """SCAN quart-session keys, DEL those belonging to user_id
    except the one matching except_session_id. Returns count revoked."""
```

Implementation: `SCAN` with `MATCH quart-session:*` (configured prefix), deserialize each value, check the `user_id` field, `DEL` if match and not exception. Used by:

1. Password change success
2. `/account/sessions/revoke` button
3. `/account/delete` (with `except_session_id=None`)

Known limitation: O(total sessions). Fine for current load. If it ever matters, add a secondary Redis set `user_sessions:{user_id}` populated on session create / destroyed on session end — then revocation becomes O(sessions for this user).

### 5.4 Synchronous routes (no background work)

- Delete account (four indexed DELETEs, single user)
- Clear watched (single DELETE)
- Save/clear filter defaults (single UPDATE)
- Theme / preference toggles (single UPDATE)

## 6. Visual consistency

The account pages match the existing design language of `login.html`, `movie.html`, and `watched_list.html`.

### 6.1 Page shell

Same `<head>` block as `login.html` and `movie.html`:
- DM Sans + Merriweather preconnect
- `static/css/output.css` with `?v={{ config.CSS_VERSION }}` cache-buster
- Pre-paint theme init script — updated to check `data-theme-server` attribute on `<html>` (set from `theme_preference`) as the fallback when `localStorage.nr-theme` is empty, preventing flash-of-wrong-theme on a new device
- Skip-to-content link for a11y (matches `login.html` pattern)
- `navbar_modern.html` and `footer_modern.html` included

### 6.2 New CSS namespace `.account-*`

Added to `static/css/input.css` alongside the existing `.auth-*`, `.watched-*`, `.movie-*`, `.filter-drawer-*` namespaces. All new classes defined in terms of existing CSS variables (`--color-bg`, `--color-text`, `--color-muted`, `--color-accent`, `--color-border`, `--font-sans`, `--font-serif`) — **zero hard-coded hex colors**, automatic light/dark theme parity.

New classes:

- `.account-page` — main layout wrapper (wider than `.auth-page`; contains tab bar + panel)
- `.account-tabs`, `.account-tab`, `.account-tab-active` — top tab bar
- `.account-panel` — active panel container
- `.account-section`, `.account-section-title` (h2), `.account-section-description` — titled section within a panel
- `.account-card` — bordered card for sub-sections (e.g., "Linked providers", "Import from Letterboxd")
- `.account-danger-card` — red-accent variant of `.account-card` for the Danger tab
- `.account-field`, `.account-field-row` — vertical label/input stack and horizontal two-column variant
- `.account-avatar`, `.account-avatar-sm/md/lg` — initials circle in three sizes
- `.account-avatar-dropdown`, `.account-avatar-dropdown-menu` — navbar avatar menu

### 6.3 Reuse (no duplication)

- Form inputs reuse `.auth-input`, `.auth-input-label`, `.auth-input-error`, `.auth-field-error`, `.auth-error` unchanged.
- Primary submit buttons reuse `.auth-submit`.
- **Two new global classes** (needed for this feature and expected to be reused by others):
  - `.btn-danger` — destructive button styling for delete/clear/revoke actions
  - `.modal`, `.modal-panel`, `.modal-backdrop` — modal primitive (no modal pattern exists in the app today; both this feature and future features need it)

### 6.4 Typography

- Section headings use `var(--font-serif, 'Merriweather', ...)` matching the movie-page H1 treatment
- Body and form labels use `var(--font-sans, 'DM Sans', ...)` matching everywhere else

### 6.5 Visual review gate

Before the feature is marked complete, a side-by-side screenshot review of `/account`, `/movie`, `/watched`, and `/login` confirms: margins, heading weights, button heights, card borders, and theme colors are consistent across pages.

## 7. Cross-cutting concerns

### 7.1 Auth

`@bp.before_request` redirects unauthenticated requests to `/login?next=<original>`. Every route assumes `session['user_id']` is set.

### 7.2 CSRF

Every POST gets `@csrf_required` (same decorator as `/next_movie`, `/filtered_movie`). Templates embed `csrf_token()` in hidden inputs (existing convention).

### 7.3 Rate limiting (via existing `@rate_limited`)

| Endpoint | Limit |
|---|---|
| `/account/password` | 5/hour |
| `/account/delete` | 3/hour |
| `/account/import/letterboxd` | 5/hour |
| `/account/export/watched.*` | 10/hour |
| Other POSTs | App default |

### 7.4 Logging

Lazy-format (per CLAUDE.md). Audit log for sensitive actions:

```python
logger.info("Account action: %s user=%s ip=%s", action, user_id, client_ip)
```

Never log passwords, new passwords, or CSV content. Log `import_id` instead of any CSV body.

### 7.5 Metrics

Via `infra.metrics_groups.safe_emit`:

- `account_action_total{action="password_change|delete|export|import|clear_watched|theme_change|...", outcome="success|failure"}`
- `letterboxd_import_duration_seconds` histogram
- `letterboxd_import_rows_total{outcome="matched|skipped|failed"}`

### 7.6 Tests

New test files under `tests/`:

- `test_account_routes.py` — per-tab happy path, auth-required, CSRF-required, rate-limit triggers
- `test_session_revocation.py` — the Redis primitive
- `test_letterboxd_import_job.py` — arq job with a mocked CSV parser
- `test_account_delete.py` — cascade correctness (all four DELETEs, Redis cleared)

Follow existing conventions: `pytest-asyncio` auto mode, mock at service layer, `patch.dict(os.environ, {...})` for env vars.

## 8. Files touched

**New files:**

- `nextreel/web/routes/account.py`
- `session/revocation.py`
- `templates/account/layout.html` (tab shell)
- `templates/account/profile.html`
- `templates/account/security.html`
- `templates/account/preferences.html`
- `templates/account/data.html`
- `templates/account/danger.html`
- `templates/account/import_progress.html`
- `tests/test_account_routes.py`
- `tests/test_session_revocation.py`
- `tests/test_letterboxd_import_job.py`
- `tests/test_account_delete.py`

**Modified files:**

- `infra/runtime_schema.py` — new columns on `users`, new `letterboxd_imports` table
- `nextreel/web/app.py` — register the account blueprint
- `templates/navbar_modern.html` — avatar dropdown for logged-in users
- `templates/set_filters.html` — "Save as default" button
- `templates/macros.html` — `user_avatar` macro
- `nextreel/workers/worker.py` — register `import_letterboxd` job
- `session/user_preferences.py` — helpers for `theme_preference` and `default_filters_json`
- `static/css/input.css` — `.account-*` namespace, `.btn-danger`, `.modal` primitive

## 9. Phase 2 readiness

The design leaves room for the deferred features without refactor churn:

- **Notifications tab** — add one more tab to the ordered list, one more blueprint sub-route, one new `user_notification_prefs` table. Requires an email provider and templates.
- **Integrations tab** — add a tab, a `user_auth_methods` table (finally migrating `users.auth_provider`/`oauth_sub` into it), and per-provider OAuth clients.
- **Email change flow** — swaps the read-only email display for an editable form with a verification round-trip. Lives in the same Profile tab.

## 10. Explicit non-goals

- No avatar upload
- No email change UI
- No 2FA / TOTP
- No user-facing audit log (server-side only)
- No GDPR/DSAR data export beyond the watched list
- No soft-delete / account restore
