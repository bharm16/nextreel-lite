# User Accounts & Watched List

## Context

Nextreel-lite currently uses anonymous, session-based browsing. Users have no persistent identity — when a session expires (8h max / 15min idle), all navigation state is lost. There is no way for a user to track which movies they've already seen, and the random movie discovery engine has no mechanism to avoid recommending movies a user has already watched.

This design adds:
1. **User accounts** (email+password and Google/Apple OAuth) so users have persistent identity
2. **Watched list** so users can mark movies they've seen
3. **Filter integration** so watched movies are excluded from discovery by default
4. **Watched list page** so users can browse and manage their watched movies

Anonymous browsing remains fully functional — accounts are optional.

---

## Database Schema

Three schema changes, all managed via `ensure_runtime_schema()` in `infra/runtime_schema.py`.

### New table: `users`

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id       CHAR(32) PRIMARY KEY,
    email         VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NULL,
    display_name  VARCHAR(100) NULL,
    auth_provider VARCHAR(20) NOT NULL DEFAULT 'email',
    oauth_sub     VARCHAR(255) NULL,
    created_at    DATETIME(6) NOT NULL,
    updated_at    DATETIME(6) NOT NULL,
    UNIQUE KEY idx_users_email (email),
    UNIQUE KEY idx_users_oauth (auth_provider, oauth_sub)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- `user_id`: `uuid4().hex` (32-char hex, matches existing session_id pattern)
- `password_hash`: NULL for OAuth-only accounts (no password set)
- `auth_provider`: `'email'`, `'google'`, or `'apple'`
- `oauth_sub`: The provider's unique subject identifier from the ID token

### New table: `user_watched_movies`

```sql
CREATE TABLE IF NOT EXISTS user_watched_movies (
    user_id    CHAR(32) NOT NULL,
    tconst     VARCHAR(16) NOT NULL,
    watched_at DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id, tconst),
    KEY idx_watched_user_date (user_id, watched_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### Alter: `user_navigation_state`

Add a nullable `user_id` column to link sessions to authenticated users:

```sql
ALTER TABLE user_navigation_state
    ADD COLUMN user_id CHAR(32) NULL AFTER session_id,
    ADD KEY idx_nav_user_id (user_id);
```

This is applied via a migration check in `ensure_runtime_schema()` (check if column exists before adding).

---

## Authentication

### Approach

- **Authlib** for OAuth 2.0 (Google + Apple Sign-In)
- **bcrypt** for password hashing
- **Session-based auth** — no JWT. The existing MySQL-backed `NavigationState` is extended with `user_id`.

### New module: `session/user_auth.py`

Core functions:

- `register_user(pool, email, password, display_name) -> user_id` — hash password, insert user, return ID
- `authenticate_user(pool, email, password) -> user_id | None` — verify credentials
- `find_or_create_oauth_user(pool, provider, oauth_sub, email, display_name) -> user_id` — idempotent OAuth user creation
- `link_session_to_user(state_store, session_id, user_id)` — set `user_id` on NavigationState
- `unlink_session_from_user(state_store, session_id)` — clear `user_id` (logout)

### OAuth configuration

New environment variables:

- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — Google OAuth credentials
- `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` — Apple Sign-In credentials
- `OAUTH_REDIRECT_BASE_URL` — Base URL for OAuth callbacks (e.g. `https://nextreel.com`)

These are optional — if not set, the corresponding OAuth buttons are hidden.

### Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/login` | GET | Show login form |
| `/login` | POST | Email+password login |
| `/register` | GET | Show registration form |
| `/register` | POST | Create account |
| `/logout` | POST | Clear user_id from session (extend existing) |
| `/auth/google` | GET | Start Google OAuth flow |
| `/auth/google/callback` | GET | Google OAuth callback |
| `/auth/apple` | GET | Start Apple OAuth flow |
| `/auth/apple/callback` | POST | Apple OAuth callback (Apple uses POST) |

### Session linking

- On login/register/OAuth, the current anonymous session's `user_id` column is set. The session (with its filters, queue, navigation state) is preserved.
- On logout, `user_id` is cleared. The session continues as anonymous.
- Multiple simultaneous sessions per user are allowed (no forced single-session).

### Security

- All POST routes use existing `@csrf_required` decorator
- Rate limiting applied to `/login` and `/register` via existing rate limiter
- Passwords: minimum 8 characters
- Password hashing: bcrypt with default rounds (12)
- OAuth state parameter stored in session to prevent CSRF
- Email uniqueness enforced at the database level

---

## Watched List

### New module: `movies/watched_store.py`

Data access layer following the same pattern as `CandidateStore` and `ProjectionStore`:

```python
class WatchedStore:
    def __init__(self, pool: DatabaseConnectionPool): ...

    async def add(self, user_id: str, tconst: str) -> None
    async def remove(self, user_id: str, tconst: str) -> None
    async def is_watched(self, user_id: str, tconst: str) -> bool
    async def list_watched(self, user_id: str, limit: int = 20, offset: int = 0) -> list[dict]
    async def watched_tconsts(self, user_id: str) -> set[str]
    async def count(self, user_id: str) -> int
```

`list_watched()` joins `user_watched_movies` with `movie_candidates` (or `movie_projection`) to return title, year, poster URL, and watched date.

### Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/watched` | GET | Paginated watched list page (login required) |
| `/watched/add/<tconst>` | POST | Mark movie as watched |
| `/watched/remove/<tconst>` | POST | Unmark movie as watched |

All POST routes use CSRF protection.

### Filter integration

**FilterState** (in `filter_contracts.py`) gets a new field:

```python
exclude_watched: bool  # Default True for logged-in users
```

**Exclusion flow** in `CandidateStore.fetch_candidate_refs()`:

1. If `user_id` is present on the session AND `exclude_watched` is True:
   - Call `WatchedStore.watched_tconsts(user_id)` to get the set of watched tconsts
   - Add them to the existing `excluded_tconsts` set
2. The existing `NOT IN` clause in the candidate query handles the rest

This reuses the exact same exclusion mechanism already used for queue/prev/future/seen movies.

**Filter normalization** in `infra/filter_normalizer.py`:
- `normalize_filters()` reads the `exclude_watched` checkbox from form data
- `default_filter_state()` sets `exclude_watched=True` when a user is logged in

**Performance consideration**: The `NOT IN` clause works well for up to a few thousand watched movies. If users accumulate very large lists (10,000+), this can be migrated to a subquery or `NOT EXISTS` approach. No premature optimization needed.

---

## UI & Templates

### Modified: `navbar_modern.html`

- **Logged out**: "Log In" button in the nav bar
- **Logged in**: User display name (or email prefix), dropdown with:
  - "My Watched List" link
  - "Log Out" button (POST form with CSRF)

### Modified: `movie_card.html`

- When logged in: "Mark as Watched" button (or "Watched" state toggle)
  - Uses an eye icon or checkmark
  - POST form with CSRF to `/watched/add/<tconst>` or `/watched/remove/<tconst>`
  - Hidden for anonymous users

### Modified: `set_filters.html`

- When logged in: checkbox "Exclude movies I've watched" in the filter form
  - Checked by default
  - Hidden for anonymous users

### New: `login.html`

- Email + password form
- Google Sign-In button
- Apple Sign-In button
- "Create an account" link to `/register`
- Error message display area

### New: `register.html`

- Email, password, confirm password form
- Google Sign-In button
- Apple Sign-In button
- "Already have an account?" link to `/login`
- Error message display area

### New: `watched_list.html`

- Paginated grid of watched movies
- Each card: poster thumbnail, title, year, date watched
- "Remove" button on each card
- Empty state: "You haven't marked any movies as watched yet"
- Pagination controls

### Styling

- All new templates follow existing Tailwind + CSS variables pattern
- Theme toggle (dark/light) works on all new pages
- Responsive design consistent with existing pages

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Duplicate email on register | Form error: "An account with this email already exists" |
| Wrong password on login | Form error: "Invalid email or password" (no reveal) |
| OAuth failure | Redirect to `/login` with flash: "Sign-in failed. Please try again." |
| OAuth email conflict | If email exists with different provider, show: "An account with this email already exists. Please log in with [provider]." |
| Password too short | Form error: "Password must be at least 8 characters" |
| Password mismatch | Form error: "Passwords do not match" |
| Not logged in for /watched | Redirect to `/login` |
| Watched movie not found | Silently succeed (idempotent) |

---

## New Dependencies

Add to `requirements.txt`:

- `authlib>=1.3.0` — OAuth 2.0 client (async-compatible)
- `bcrypt>=4.0.0` — Password hashing
- `email-validator>=2.0.0` — Email format validation

---

## Testing

- Unit tests for `WatchedStore` (add, remove, is_watched, list, count)
- Unit tests for `user_auth` (register, authenticate, find_or_create_oauth)
- Integration test: filter exclusion with watched movies
- Integration test: anonymous browsing still works without login
- Test OAuth find-or-create idempotency
- Test session linking on login and unlinking on logout

---

## Files to Create or Modify

### New files
- `session/user_auth.py` — Registration, login, OAuth logic
- `movies/watched_store.py` — Watched list data access
- `templates/login.html` — Login page
- `templates/register.html` — Registration page
- `templates/watched_list.html` — Watched list page

### Modified files
- `infra/runtime_schema.py` — Add `users`, `user_watched_movies` tables; alter `user_navigation_state`
- `infra/navigation_state.py` — Add `user_id` field to `NavigationState` dataclass
- `filter_contracts.py` — Add `exclude_watched` to `FilterState`
- `infra/filter_normalizer.py` — Handle `exclude_watched` in normalization
- `movies/candidate_store.py` — Pass watched tconsts to exclusion set
- `movie_navigator.py` — Thread watched exclusion through navigation
- `movie_service.py` — Wire `WatchedStore` into `MovieManager`
- `routes.py` — Add auth and watched-list routes
- `app.py` — Initialize Authlib, `WatchedStore`; OAuth config
- `settings.py` — Add OAuth config fields
- `templates/navbar_modern.html` — Auth state in nav
- `templates/movie_card.html` — Watched toggle button
- `templates/set_filters.html` — Exclude watched checkbox
- `requirements.txt` — Add authlib, bcrypt, email-validator
