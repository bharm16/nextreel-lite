# User Accounts & Watched List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add user accounts (email+password and Google/Apple OAuth), a watched-list feature, and a filter toggle to exclude watched movies from discovery.

**Architecture:** Session-based auth extending the existing MySQL-backed `NavigationState` with a `user_id` column. New `users` and `user_watched_movies` tables created via the existing `ensure_runtime_schema()` pattern. Watched movie exclusion plugs into the existing `NOT IN` clause in `CandidateStore._build_candidate_query()`. Authlib handles OAuth flows; bcrypt handles password hashing.

**Tech Stack:** Quart, aiomysql, Authlib, bcrypt, email-validator

**Spec:** `docs/superpowers/specs/2026-04-05-user-accounts-watched-list-design.md`

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `session/user_auth.py` | User registration, login, password hashing, OAuth find-or-create |
| `movies/watched_store.py` | CRUD for `user_watched_movies` table |
| `templates/login.html` | Login page (email+password form + OAuth buttons) |
| `templates/register.html` | Registration page |
| `templates/watched_list.html` | Paginated watched-list page |
| `tests/test_user_auth.py` | Tests for registration, login, OAuth |
| `tests/test_watched_store.py` | Tests for watched-list CRUD |
| `tests/test_watched_filter.py` | Tests for filter exclusion integration |

### Modified files
| File | Change |
|------|--------|
| `requirements.txt` | Add authlib, bcrypt, email-validator |
| `infra/runtime_schema.py` | Add `users`, `user_watched_movies` CREATE TABLE; add `user_id` column migration |
| `infra/navigation_state.py` | Add `user_id` field to `NavigationState` dataclass; update serialization/deserialization |
| `filter_contracts.py` | Add `exclude_watched: bool` to `FilterState` |
| `infra/filter_normalizer.py` | Handle `exclude_watched` in `normalize_filters()` and `default_filter_state()` |
| `movie_navigator.py` | Accept and thread `watched_tconsts` through `_excluded_tconsts()` and `_refill_queue()` |
| `movies/candidate_store.py` | No change needed — watched tconsts are merged into `excluded_tconsts` in `MovieNavigator._refill_queue()` before calling `fetch_candidate_refs()` |
| `movie_service.py` | Wire `WatchedStore`, expose watched-list methods, pass watched exclusions |
| `routes.py` | Add auth routes, watched-list routes, inject `user` into templates |
| `app.py` | Initialize Authlib OAuth client, add OAuth config |
| `templates/navbar_modern.html` | Auth state (login/logout/user menu) |
| `templates/movie_card.html` | "Mark as Watched" toggle button |
| `templates/set_filters.html` | "Exclude watched" checkbox |

---

## Task 1: Add Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add new dependencies**

Add these lines to `requirements.txt` after the existing `cryptography` line (in the "Security & Authentication" section):

```
authlib>=1.3.0                   # OAuth 2.0 client for Google/Apple Sign-In
bcrypt>=4.0.0                    # Password hashing
email-validator>=2.0.0           # Email format validation
```

- [ ] **Step 2: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: All packages install successfully.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add authlib, bcrypt, email-validator dependencies"
```

---

## Task 2: Database Schema — New Tables and Column Migration

**Files:**
- Modify: `infra/runtime_schema.py`
- Test: `tests/test_runtime_schema.py`

- [ ] **Step 1: Write the failing test for new tables**

Add to `tests/test_runtime_schema.py`:

```python
async def test_ensure_runtime_schema_creates_users_table(mock_db_pool):
    """Verify that ensure_runtime_schema creates the users table."""
    from infra.runtime_schema import _RUNTIME_SCHEMA_STATEMENTS

    users_sql = [s for s in _RUNTIME_SCHEMA_STATEMENTS if "CREATE TABLE IF NOT EXISTS users" in s]
    assert len(users_sql) == 1, "Expected exactly one CREATE TABLE for users"
    assert "user_id" in users_sql[0]
    assert "email" in users_sql[0]
    assert "password_hash" in users_sql[0]
    assert "auth_provider" in users_sql[0]
    assert "oauth_sub" in users_sql[0]


async def test_ensure_runtime_schema_creates_watched_table(mock_db_pool):
    """Verify that ensure_runtime_schema creates the user_watched_movies table."""
    from infra.runtime_schema import _RUNTIME_SCHEMA_STATEMENTS

    watched_sql = [s for s in _RUNTIME_SCHEMA_STATEMENTS if "CREATE TABLE IF NOT EXISTS user_watched_movies" in s]
    assert len(watched_sql) == 1, "Expected exactly one CREATE TABLE for user_watched_movies"
    assert "user_id" in watched_sql[0]
    assert "tconst" in watched_sql[0]
    assert "watched_at" in watched_sql[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_runtime_schema.py::test_ensure_runtime_schema_creates_users_table tests/test_runtime_schema.py::test_ensure_runtime_schema_creates_watched_table -v`
Expected: FAIL — no matching SQL statements found.

- [ ] **Step 3: Add the users and user_watched_movies CREATE TABLE statements**

In `infra/runtime_schema.py`, add two new entries to the `_RUNTIME_SCHEMA_STATEMENTS` tuple (after the existing `movie_candidates` entry, before the closing `)`):

```python
    """
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS user_watched_movies (
        user_id    CHAR(32) NOT NULL,
        tconst     VARCHAR(16) NOT NULL,
        watched_at DATETIME(6) NOT NULL,
        PRIMARY KEY (user_id, tconst),
        KEY idx_watched_user_date (user_id, watched_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_runtime_schema.py::test_ensure_runtime_schema_creates_users_table tests/test_runtime_schema.py::test_ensure_runtime_schema_creates_watched_table -v`
Expected: PASS

- [ ] **Step 5: Add the user_id column migration function**

Add a new function to `infra/runtime_schema.py` after the existing migration functions:

```python
async def ensure_user_navigation_user_id_column(db_pool) -> None:
    """Add the additive user_id column to link sessions to user accounts."""
    present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'user_navigation_state'
          AND column_name = 'user_id'
        LIMIT 1
        """,
        fetch="one",
    )
    if present:
        return

    await db_pool.execute(
        """
        ALTER TABLE user_navigation_state
        ADD COLUMN user_id CHAR(32) NULL AFTER session_id,
        ADD KEY idx_nav_user_id (user_id)
        """,
        fetch="none",
    )
    logger.info("Added user_navigation_state.user_id")
```

- [ ] **Step 6: Call the migration from ensure_runtime_schema()**

In `ensure_runtime_schema()`, add a call after the existing migrations:

```python
    await ensure_user_navigation_user_id_column(db_pool)
```

So the function becomes:
```python
async def ensure_runtime_schema(db_pool) -> None:
    """Create runtime-owned tables if they do not already exist."""
    for statement in _RUNTIME_SCHEMA_STATEMENTS:
        await db_pool.execute(statement, fetch="none")
    await ensure_user_navigation_current_ref_column(db_pool)
    await ensure_movie_candidates_shuffle_key(db_pool)
    await ensure_movie_candidates_refreshed_at_index(db_pool)
    await ensure_user_navigation_user_id_column(db_pool)
    logger.info("Runtime schema ensured")
```

- [ ] **Step 7: Run full runtime schema test suite**

Run: `python3 -m pytest tests/test_runtime_schema.py -v`
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add infra/runtime_schema.py tests/test_runtime_schema.py
git commit -m "feat: add users and user_watched_movies tables; add user_id column migration"
```

---

## Task 3: Extend NavigationState with user_id

**Files:**
- Modify: `infra/navigation_state.py`
- Test: `tests/test_navigation_state.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_navigation_state.py`:

```python
async def test_navigation_state_has_user_id_field():
    """NavigationState should have an optional user_id field."""
    from infra.navigation_state import NavigationState, default_filter_state
    from infra.time_utils import utcnow

    now = utcnow()
    state = NavigationState(
        session_id="test123",
        version=1,
        csrf_token="csrf",
        filters=default_filter_state(),
        current_tconst=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
        user_id=None,
    )
    assert state.user_id is None

    state.user_id = "abc123"
    assert state.user_id == "abc123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_navigation_state.py::test_navigation_state_has_user_id_field -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'user_id'`

- [ ] **Step 3: Add user_id to NavigationState dataclass**

In `infra/navigation_state.py`, add `user_id` field to the `NavigationState` dataclass (line 105, after `current_ref`):

```python
    current_ref: dict[str, Any] | None = None
    user_id: str | None = None
```

- [ ] **Step 4: Update clone() to include user_id**

In `NavigationState.clone()`, add `user_id=self.user_id` to the constructor call:

```python
    def clone(self) -> "NavigationState":
        return NavigationState(
            session_id=self.session_id,
            version=self.version,
            csrf_token=self.csrf_token,
            filters=copy.deepcopy(self.filters),
            current_tconst=self.current_tconst,
            queue=copy.deepcopy(self.queue),
            prev=copy.deepcopy(self.prev),
            future=copy.deepcopy(self.future),
            seen=list(self.seen),
            created_at=self.created_at,
            last_activity_at=self.last_activity_at,
            expires_at=self.expires_at,
            current_ref=copy.deepcopy(self.current_ref),
            user_id=self.user_id,
        )
```

- [ ] **Step 5: Update _row_to_state() to read user_id from DB row**

In `NavigationStateStore._row_to_state()`, add `user_id=row.get("user_id")` to the constructor:

```python
    def _row_to_state(self, row: dict[str, Any]) -> NavigationState:
        filters = self._json_load(row.get("filters_json"), default_filter_state())
        current_ref = _normalize_ref(self._json_load(row.get("current_ref_json"), None))
        return NavigationState(
            session_id=row["session_id"],
            version=int(row["version"]),
            csrf_token=row["csrf_token"],
            filters=filters if isinstance(filters, dict) else default_filter_state(),
            current_tconst=row.get("current_tconst"),
            queue=_normalize_ref_list(self._json_load(row.get("queue_json"), []), max_items=QUEUE_TARGET),
            prev=_normalize_ref_list(self._json_load(row.get("prev_json"), []), max_items=PREV_STACK_MAX),
            future=_normalize_ref_list(self._json_load(row.get("future_json"), []), max_items=FUTURE_STACK_MAX),
            seen=_normalize_seen(self._json_load(row.get("seen_json"), [])),
            created_at=row["created_at"],
            last_activity_at=row["last_activity_at"],
            expires_at=row["expires_at"],
            current_ref=current_ref,
            user_id=row.get("user_id"),
        )
```

- [ ] **Step 6: Update _load_row() SELECT to include user_id**

In `_load_row()`, add `user_id` to the SELECT column list:

```python
    async def _load_row(self, session_id: str) -> NavigationState | None:
        row = await self.db_pool.execute(
            """
            SELECT session_id, user_id, version, csrf_token, filters_json, current_tconst, current_ref_json,
                   queue_json, prev_json, future_json, seen_json,
                   created_at, last_activity_at, expires_at
            FROM user_navigation_state
            WHERE session_id = %s
            """,
            [session_id],
            fetch="one",
        )
        if not row:
            return None
        return self._row_to_state(row)
```

- [ ] **Step 7: Update _insert_state() to include user_id**

In `_insert_state()`, add `user_id` to the INSERT:

```python
    async def _insert_state(self, state: NavigationState) -> None:
        serialized = self._serialized_state_fields(state)
        await self.db_pool.execute(
            """
            INSERT INTO user_navigation_state (
                session_id, user_id, version, csrf_token, filters_json, current_tconst, current_ref_json,
                queue_json, prev_json, future_json, seen_json,
                created_at, last_activity_at, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                state.session_id,
                state.user_id,
                state.version,
                serialized["csrf_token"],
                serialized["filters_json"],
                serialized["current_tconst"],
                serialized["current_ref_json"],
                serialized["queue_json"],
                serialized["prev_json"],
                serialized["future_json"],
                serialized["seen_json"],
                state.created_at,
                state.last_activity_at,
                state.expires_at,
            ],
            fetch="none",
        )
```

- [ ] **Step 8: Add set_user_id() method to NavigationStateStore**

Add a method to update just the `user_id` column on a session:

```python
    async def set_user_id(self, session_id: str, user_id: str | None) -> None:
        """Link or unlink a user account to/from a session."""
        await self.db_pool.execute(
            """
            UPDATE user_navigation_state
            SET user_id = %s, last_activity_at = %s
            WHERE session_id = %s
            """,
            [user_id, utcnow(), session_id],
            fetch="none",
        )
```

- [ ] **Step 9: Run tests**

Run: `python3 -m pytest tests/test_navigation_state.py -v`
Expected: All tests PASS.

- [ ] **Step 10: Commit**

```bash
git add infra/navigation_state.py tests/test_navigation_state.py
git commit -m "feat: add user_id field to NavigationState and wire through serialization"
```

---

## Task 4: User Auth Module

**Files:**
- Create: `session/user_auth.py`
- Create: `tests/test_user_auth.py`

- [ ] **Step 1: Write failing tests for registration and authentication**

Create `tests/test_user_auth.py`:

```python
"""Tests for user authentication logic."""

from unittest.mock import AsyncMock, patch

import pytest


async def test_register_user_returns_user_id(mock_db_pool):
    from session.user_auth import register_user

    mock_db_pool.execute = AsyncMock(return_value=None)

    user_id = await register_user(
        mock_db_pool,
        email="test@example.com",
        password="securepass123",
        display_name="Test User",
    )

    assert user_id is not None
    assert len(user_id) == 32  # uuid4().hex


async def test_register_user_hashes_password(mock_db_pool):
    from session.user_auth import register_user

    calls = []
    async def capture_execute(query, params=None, fetch="none"):
        calls.append((query, params))
        return None

    mock_db_pool.execute = capture_execute

    await register_user(mock_db_pool, "test@example.com", "mypassword", "Test")

    insert_call = [c for c in calls if "INSERT INTO users" in c[0]]
    assert len(insert_call) == 1
    params = insert_call[0][1]
    # password_hash is the 3rd param (index 2) after user_id, email
    password_hash = params[2]
    assert password_hash != "mypassword"
    assert password_hash.startswith("$2b$")


async def test_authenticate_user_valid_password(mock_db_pool):
    import bcrypt
    from session.user_auth import authenticate_user

    hashed = bcrypt.hashpw(b"correctpassword", bcrypt.gensalt()).decode("utf-8")
    mock_db_pool.execute = AsyncMock(return_value={
        "user_id": "abc123",
        "password_hash": hashed,
    })

    result = await authenticate_user(mock_db_pool, "test@example.com", "correctpassword")
    assert result == "abc123"


async def test_authenticate_user_wrong_password(mock_db_pool):
    import bcrypt
    from session.user_auth import authenticate_user

    hashed = bcrypt.hashpw(b"correctpassword", bcrypt.gensalt()).decode("utf-8")
    mock_db_pool.execute = AsyncMock(return_value={
        "user_id": "abc123",
        "password_hash": hashed,
    })

    result = await authenticate_user(mock_db_pool, "test@example.com", "wrongpassword")
    assert result is None


async def test_authenticate_user_not_found(mock_db_pool):
    from session.user_auth import authenticate_user

    mock_db_pool.execute = AsyncMock(return_value=None)

    result = await authenticate_user(mock_db_pool, "nobody@example.com", "password")
    assert result is None


async def test_find_or_create_oauth_user_creates_new(mock_db_pool):
    from session.user_auth import find_or_create_oauth_user

    # First call returns None (user not found), second is the INSERT
    mock_db_pool.execute = AsyncMock(side_effect=[None, None])

    user_id = await find_or_create_oauth_user(
        mock_db_pool,
        provider="google",
        oauth_sub="google-12345",
        email="user@gmail.com",
        display_name="Google User",
    )
    assert user_id is not None
    assert len(user_id) == 32


async def test_find_or_create_oauth_user_finds_existing(mock_db_pool):
    from session.user_auth import find_or_create_oauth_user

    mock_db_pool.execute = AsyncMock(return_value={"user_id": "existing123"})

    user_id = await find_or_create_oauth_user(
        mock_db_pool,
        provider="google",
        oauth_sub="google-12345",
        email="user@gmail.com",
        display_name="Google User",
    )
    assert user_id == "existing123"


async def test_validate_registration_rejects_short_password():
    from session.user_auth import validate_registration

    errors = validate_registration("test@example.com", "short", "short")
    assert "password" in errors


async def test_validate_registration_rejects_mismatched_passwords():
    from session.user_auth import validate_registration

    errors = validate_registration("test@example.com", "password123", "password456")
    assert "confirm_password" in errors


async def test_validate_registration_rejects_invalid_email():
    from session.user_auth import validate_registration

    errors = validate_registration("not-an-email", "password123", "password123")
    assert "email" in errors
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_user_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'session.user_auth'`

- [ ] **Step 3: Create session/user_auth.py**

Create `session/user_auth.py`:

```python
"""User registration, login, and OAuth account management."""

from __future__ import annotations

from uuid import uuid4

import bcrypt
from email_validator import EmailNotValidError, validate_email

from infra.time_utils import utcnow
from logging_config import get_logger

logger = get_logger(__name__)

MIN_PASSWORD_LENGTH = 8


def validate_registration(
    email: str, password: str, confirm_password: str
) -> dict[str, str]:
    """Validate registration form inputs. Returns {field: error_message} dict."""
    errors: dict[str, str] = {}

    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        errors["email"] = "Please enter a valid email address."

    if len(password) < MIN_PASSWORD_LENGTH:
        errors["password"] = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."

    if password != confirm_password:
        errors["confirm_password"] = "Passwords do not match."

    return errors


async def register_user(
    db_pool,
    email: str,
    password: str,
    display_name: str | None = None,
) -> str:
    """Create a new email+password user. Returns user_id."""
    user_id = uuid4().hex
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    now = utcnow()

    await db_pool.execute(
        """
        INSERT INTO users (user_id, email, password_hash, display_name,
                           auth_provider, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        [user_id, email.lower().strip(), password_hash, display_name, "email", now, now],
        fetch="none",
    )
    logger.info("Registered user %s via email", user_id)
    return user_id


async def authenticate_user(
    db_pool, email: str, password: str
) -> str | None:
    """Verify email+password credentials. Returns user_id or None."""
    row = await db_pool.execute(
        """
        SELECT user_id, password_hash
        FROM users
        WHERE email = %s AND auth_provider = 'email'
        """,
        [email.lower().strip()],
        fetch="one",
    )
    if not row or not row.get("password_hash"):
        return None

    if bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        return row["user_id"]
    return None


async def find_or_create_oauth_user(
    db_pool,
    *,
    provider: str,
    oauth_sub: str,
    email: str,
    display_name: str | None = None,
) -> str:
    """Find an existing OAuth user or create a new one. Returns user_id."""
    row = await db_pool.execute(
        """
        SELECT user_id FROM users
        WHERE auth_provider = %s AND oauth_sub = %s
        """,
        [provider, oauth_sub],
        fetch="one",
    )
    if row:
        return row["user_id"]

    user_id = uuid4().hex
    now = utcnow()
    await db_pool.execute(
        """
        INSERT INTO users (user_id, email, password_hash, display_name,
                           auth_provider, oauth_sub, created_at, updated_at)
        VALUES (%s, %s, NULL, %s, %s, %s, %s, %s)
        """,
        [user_id, email.lower().strip(), display_name, provider, oauth_sub, now, now],
        fetch="none",
    )
    logger.info("Created OAuth user %s via %s", user_id, provider)
    return user_id


async def get_user_by_id(db_pool, user_id: str) -> dict | None:
    """Fetch user record by user_id."""
    return await db_pool.execute(
        "SELECT user_id, email, display_name, auth_provider, created_at FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )


async def get_user_by_email(db_pool, email: str) -> dict | None:
    """Fetch user record by email."""
    return await db_pool.execute(
        "SELECT user_id, email, display_name, auth_provider FROM users WHERE email = %s",
        [email.lower().strip()],
        fetch="one",
    )
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_user_auth.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add session/user_auth.py tests/test_user_auth.py
git commit -m "feat: add user auth module with registration, login, and OAuth"
```

---

## Task 5: Watched Store

**Files:**
- Create: `movies/watched_store.py`
- Create: `tests/test_watched_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_watched_store.py`:

```python
"""Tests for WatchedStore CRUD operations."""

from unittest.mock import AsyncMock

import pytest


async def test_add_watched_movie(mock_db_pool):
    from movies.watched_store import WatchedStore

    mock_db_pool.execute = AsyncMock(return_value=None)
    store = WatchedStore(mock_db_pool)

    await store.add("user123", "tt0111161")

    mock_db_pool.execute.assert_called_once()
    call_args = mock_db_pool.execute.call_args
    assert "INSERT" in call_args[0][0]
    assert "user123" in call_args[0][1]
    assert "tt0111161" in call_args[0][1]


async def test_remove_watched_movie(mock_db_pool):
    from movies.watched_store import WatchedStore

    mock_db_pool.execute = AsyncMock(return_value=None)
    store = WatchedStore(mock_db_pool)

    await store.remove("user123", "tt0111161")

    mock_db_pool.execute.assert_called_once()
    call_args = mock_db_pool.execute.call_args
    assert "DELETE" in call_args[0][0]


async def test_is_watched_returns_true(mock_db_pool):
    from movies.watched_store import WatchedStore

    mock_db_pool.execute = AsyncMock(return_value={"cnt": 1})
    store = WatchedStore(mock_db_pool)

    result = await store.is_watched("user123", "tt0111161")
    assert result is True


async def test_is_watched_returns_false(mock_db_pool):
    from movies.watched_store import WatchedStore

    mock_db_pool.execute = AsyncMock(return_value=None)
    store = WatchedStore(mock_db_pool)

    result = await store.is_watched("user123", "tt9999999")
    assert result is False


async def test_watched_tconsts_returns_set(mock_db_pool):
    from movies.watched_store import WatchedStore

    mock_db_pool.execute = AsyncMock(return_value=[
        {"tconst": "tt0111161"},
        {"tconst": "tt0068646"},
    ])
    store = WatchedStore(mock_db_pool)

    result = await store.watched_tconsts("user123")
    assert result == {"tt0111161", "tt0068646"}


async def test_watched_tconsts_empty(mock_db_pool):
    from movies.watched_store import WatchedStore

    mock_db_pool.execute = AsyncMock(return_value=[])
    store = WatchedStore(mock_db_pool)

    result = await store.watched_tconsts("user123")
    assert result == set()


async def test_count_watched(mock_db_pool):
    from movies.watched_store import WatchedStore

    mock_db_pool.execute = AsyncMock(return_value={"cnt": 42})
    store = WatchedStore(mock_db_pool)

    result = await store.count("user123")
    assert result == 42


async def test_list_watched_returns_movies(mock_db_pool):
    from movies.watched_store import WatchedStore

    mock_db_pool.execute = AsyncMock(return_value=[
        {"tconst": "tt0111161", "primaryTitle": "The Shawshank Redemption",
         "startYear": 1994, "watched_at": "2026-01-01"},
    ])
    store = WatchedStore(mock_db_pool)

    result = await store.list_watched("user123", limit=20, offset=0)
    assert len(result) == 1
    assert result[0]["tconst"] == "tt0111161"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_watched_store.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create movies/watched_store.py**

```python
"""CRUD operations for the user_watched_movies table."""

from __future__ import annotations

from typing import Any

from infra.time_utils import utcnow
from logging_config import get_logger

logger = get_logger(__name__)


class WatchedStore:
    """Data access layer for user watched-movie tracking."""

    def __init__(self, db_pool):
        self.db_pool = db_pool

    async def add(self, user_id: str, tconst: str) -> None:
        """Mark a movie as watched (idempotent)."""
        await self.db_pool.execute(
            """
            INSERT INTO user_watched_movies (user_id, tconst, watched_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE watched_at = VALUES(watched_at)
            """,
            [user_id, tconst, utcnow()],
            fetch="none",
        )

    async def remove(self, user_id: str, tconst: str) -> None:
        """Remove a movie from the watched list."""
        await self.db_pool.execute(
            "DELETE FROM user_watched_movies WHERE user_id = %s AND tconst = %s",
            [user_id, tconst],
            fetch="none",
        )

    async def is_watched(self, user_id: str, tconst: str) -> bool:
        """Check if a specific movie is in the user's watched list."""
        row = await self.db_pool.execute(
            "SELECT 1 AS cnt FROM user_watched_movies WHERE user_id = %s AND tconst = %s",
            [user_id, tconst],
            fetch="one",
        )
        return row is not None

    async def watched_tconsts(self, user_id: str) -> set[str]:
        """Return the set of all watched tconsts for a user."""
        rows = await self.db_pool.execute(
            "SELECT tconst FROM user_watched_movies WHERE user_id = %s",
            [user_id],
            fetch="all",
        )
        if not rows:
            return set()
        return {row["tconst"] for row in rows}

    async def count(self, user_id: str) -> int:
        """Return the count of watched movies for a user."""
        row = await self.db_pool.execute(
            "SELECT COUNT(*) AS cnt FROM user_watched_movies WHERE user_id = %s",
            [user_id],
            fetch="one",
        )
        return row["cnt"] if row else 0

    async def list_watched(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return watched movies with metadata, ordered by most recently watched."""
        rows = await self.db_pool.execute(
            """
            SELECT w.tconst, w.watched_at,
                   c.primaryTitle, c.startYear, c.genres, c.slug
            FROM user_watched_movies w
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            WHERE w.user_id = %s
            ORDER BY w.watched_at DESC
            LIMIT %s OFFSET %s
            """,
            [user_id, limit, offset],
            fetch="all",
        )
        return rows if rows else []
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_watched_store.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add movies/watched_store.py tests/test_watched_store.py
git commit -m "feat: add WatchedStore for watched-list CRUD operations"
```

---

## Task 6: FilterState — Add exclude_watched Field

**Files:**
- Modify: `filter_contracts.py`
- Modify: `infra/filter_normalizer.py`
- Test: `tests/test_filter_normalizer.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_filter_normalizer.py`:

```python
async def test_normalize_filters_reads_exclude_watched():
    """normalize_filters should read the exclude_watched checkbox from form data."""
    from unittest.mock import MagicMock
    from infra.filter_normalizer import normalize_filters

    form_data = MagicMock()
    form_data.get = lambda key, default=None: {
        "exclude_watched": "on",
    }.get(key, default)
    form_data.getlist = lambda key: []

    filters = normalize_filters(form_data)
    assert filters.get("exclude_watched") is True


async def test_normalize_filters_exclude_watched_absent_defaults_true():
    """When exclude_watched is not in form data, it should default to True."""
    from unittest.mock import MagicMock
    from infra.filter_normalizer import normalize_filters

    form_data = MagicMock()
    form_data.get = lambda key, default=None: None
    form_data.getlist = lambda key: []

    filters = normalize_filters(form_data)
    assert filters.get("exclude_watched") is True


async def test_default_filter_state_includes_exclude_watched():
    from infra.filter_normalizer import default_filter_state

    filters = default_filter_state()
    assert "exclude_watched" in filters
    assert filters["exclude_watched"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_filter_normalizer.py::test_normalize_filters_reads_exclude_watched tests/test_filter_normalizer.py::test_default_filter_state_includes_exclude_watched -v`
Expected: FAIL

- [ ] **Step 3: Add exclude_watched to FilterState**

In `filter_contracts.py`, add the field to `FilterState`:

```python
class FilterState(TypedDict, total=False):
    year_min: int | str
    year_max: int | str
    imdb_score_min: float | str
    imdb_score_max: float | str
    num_votes_min: int | str
    num_votes_max: int | str
    language: str
    genres_selected: list[str]
    exclude_watched: bool
```

- [ ] **Step 4: Update default_filter_state()**

In `infra/filter_normalizer.py`, add `exclude_watched` to the return dict in `default_filter_state()`:

```python
def default_filter_state(current_year: int | None = None) -> FilterState:
    year = current_year or utcnow().year
    return {
        "year_min": 1900,
        "year_max": year,
        "imdb_score_min": 7.0,
        "imdb_score_max": 10.0,
        "num_votes_min": 100000,
        "num_votes_max": 200000,
        "language": "en",
        "genres_selected": [],
        "exclude_watched": True,
    }
```

- [ ] **Step 5: Update normalize_filters()**

In `infra/filter_normalizer.py`, add handling for `exclude_watched` at the end of `normalize_filters()` (before the `return filters` line):

```python
    # exclude_watched checkbox: "on" means checked, absence means use default (True)
    exclude_watched_raw = form_data.get("exclude_watched")
    if exclude_watched_raw == "off":
        filters["exclude_watched"] = False
    else:
        filters["exclude_watched"] = True

    return filters
```

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_filter_normalizer.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add filter_contracts.py infra/filter_normalizer.py tests/test_filter_normalizer.py
git commit -m "feat: add exclude_watched field to FilterState and filter normalization"
```

---

## Task 7: Wire Watched Exclusion Through Navigation

**Files:**
- Modify: `movies/candidate_store.py`
- Modify: `movie_navigator.py`
- Modify: `movie_service.py`
- Create: `tests/test_watched_filter.py`

- [ ] **Step 1: Write failing integration test**

Create `tests/test_watched_filter.py`:

```python
"""Tests for watched-movie exclusion in candidate fetching."""

from unittest.mock import AsyncMock, MagicMock

import pytest


async def test_refill_queue_merges_watched_tconsts(mock_db_pool):
    """_refill_queue should merge watched tconsts into the excluded set."""
    from movies.candidate_store import CandidateStore
    from movies.watched_store import WatchedStore
    from movie_navigator import MovieNavigator
    from infra.navigation_state import NavigationStateStore, default_filter_state

    candidate_store = CandidateStore(mock_db_pool)
    nav_state_store = MagicMock(spec=NavigationStateStore)
    watched_store = WatchedStore(mock_db_pool)

    navigator = MovieNavigator(candidate_store, nav_state_store, watched_store=watched_store)

    # Mock the watched store to return specific tconsts
    watched_store.watched_tconsts = AsyncMock(return_value={"tt9999999", "tt8888888"})

    # Mock candidate_store to capture the excluded_tconsts
    captured_excluded = []
    async def mock_fetch(filters, excluded, limit):
        captured_excluded.append(excluded)
        return [{"tconst": "tt0000001", "title": "New Movie", "slug": "new-movie"}]

    candidate_store.fetch_candidate_refs = mock_fetch

    # Build a state with a user_id and exclude_watched=True
    state = MagicMock()
    state.user_id = "user123"
    state.filters = {**default_filter_state(), "exclude_watched": True}
    state.queue = []
    state.prev = []
    state.future = []
    state.seen = []
    state.current_tconst = None

    await navigator._refill_queue(state, 5)

    assert len(captured_excluded) == 1
    assert "tt9999999" in captured_excluded[0]
    assert "tt8888888" in captured_excluded[0]


async def test_refill_queue_skips_watched_when_exclude_off(mock_db_pool):
    """When exclude_watched=False, watched tconsts should NOT be excluded."""
    from movies.candidate_store import CandidateStore
    from movies.watched_store import WatchedStore
    from movie_navigator import MovieNavigator
    from infra.navigation_state import NavigationStateStore, default_filter_state

    candidate_store = CandidateStore(mock_db_pool)
    nav_state_store = MagicMock(spec=NavigationStateStore)
    watched_store = WatchedStore(mock_db_pool)

    navigator = MovieNavigator(candidate_store, nav_state_store, watched_store=watched_store)

    watched_store.watched_tconsts = AsyncMock(return_value={"tt9999999"})

    captured_excluded = []
    async def mock_fetch(filters, excluded, limit):
        captured_excluded.append(excluded)
        return [{"tconst": "tt0000001", "title": "Movie", "slug": "movie"}]

    candidate_store.fetch_candidate_refs = mock_fetch

    state = MagicMock()
    state.user_id = "user123"
    state.filters = {**default_filter_state(), "exclude_watched": False}
    state.queue = []
    state.prev = []
    state.future = []
    state.seen = []
    state.current_tconst = None

    await navigator._refill_queue(state, 5)

    assert len(captured_excluded) == 1
    assert "tt9999999" not in captured_excluded[0]


async def test_refill_queue_no_user_id_skips_watched(mock_db_pool):
    """When no user_id on state, watched exclusion should be skipped."""
    from movies.candidate_store import CandidateStore
    from movies.watched_store import WatchedStore
    from movie_navigator import MovieNavigator
    from infra.navigation_state import NavigationStateStore, default_filter_state

    candidate_store = CandidateStore(mock_db_pool)
    nav_state_store = MagicMock(spec=NavigationStateStore)
    watched_store = WatchedStore(mock_db_pool)

    navigator = MovieNavigator(candidate_store, nav_state_store, watched_store=watched_store)

    watched_store.watched_tconsts = AsyncMock(return_value=set())

    captured_excluded = []
    async def mock_fetch(filters, excluded, limit):
        captured_excluded.append(excluded)
        return [{"tconst": "tt0000001", "title": "Movie", "slug": "movie"}]

    candidate_store.fetch_candidate_refs = mock_fetch

    state = MagicMock()
    state.user_id = None
    state.filters = default_filter_state()
    state.queue = []
    state.prev = []
    state.future = []
    state.seen = []
    state.current_tconst = None

    await navigator._refill_queue(state, 5)

    # watched_tconsts should NOT have been called
    watched_store.watched_tconsts.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_watched_filter.py -v`
Expected: FAIL — `MovieNavigator.__init__() got an unexpected keyword argument 'watched_store'`

- [ ] **Step 3: Modify MovieNavigator to accept watched_store**

In `movie_navigator.py`, update `__init__` and `_refill_queue`:

```python
class MovieNavigator:
    """State-aware next/previous/filter navigation."""

    def __init__(self, candidate_store, navigation_state_store, watched_store=None):
        self.candidate_store = candidate_store
        self.navigation_state_store = navigation_state_store
        self.watched_store = watched_store
```

Update `_refill_queue()` to merge watched tconsts:

```python
    async def _refill_queue(self, state, desired_size: int) -> None:
        missing = max(0, desired_size - len(state.queue))
        if missing <= 0:
            return

        excluded = self._excluded_tconsts(state)

        # Merge watched movies into excluded set if user is logged in and filter is on
        if (
            self.watched_store
            and getattr(state, "user_id", None)
            and state.filters.get("exclude_watched", True)
        ):
            watched = await self.watched_store.watched_tconsts(state.user_id)
            excluded |= watched

        refs = await self.candidate_store.fetch_candidate_refs(
            state.filters,
            excluded,
            missing,
        )
        if refs:
            state.queue.extend(refs)
            state.queue = state.queue[:QUEUE_TARGET]
```

- [ ] **Step 4: Update MovieManager to pass watched_store to MovieNavigator**

In `movie_service.py`, import `WatchedStore` and wire it:

Add import at top:
```python
from movies.watched_store import WatchedStore
```

In `__init__`, add:
```python
        self.watched_store = WatchedStore(self.db_pool)
```

In `start()`, update the navigator construction:
```python
        self._navigator = MovieNavigator(
            self.candidate_store,
            self.navigation_state_store,
            watched_store=self.watched_store,
        )
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_watched_filter.py -v`
Expected: All PASS.

- [ ] **Step 6: Run existing tests to check for regressions**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add movie_navigator.py movie_service.py movies/watched_store.py tests/test_watched_filter.py
git commit -m "feat: wire watched-movie exclusion through navigation and candidate fetching"
```

---

## Task 8: Auth Routes and Watched-List Routes

**Files:**
- Modify: `routes.py`
- Modify: `app.py`

- [ ] **Step 1: Add a login_required helper to routes.py**

Add near the top of `routes.py` (after `_current_state()`):

```python
def _current_user_id() -> str | None:
    """Return the user_id from the current navigation state, or None if anonymous."""
    state = getattr(g, "navigation_state", None)
    return getattr(state, "user_id", None) if state else None


def _require_login():
    """Abort with redirect to login if the user is not authenticated."""
    if not _current_user_id():
        return redirect(url_for("main.login_page"))
    return None
```

- [ ] **Step 2: Add auth routes to routes.py**

Add registration, login, and logout routes:

```python
@bp.route("/login")
async def login_page():
    if _current_user_id():
        return redirect(url_for("main.home"))
    return await render_template("login.html", errors={})


@bp.route("/login", methods=["POST"])
@csrf_required
@rate_limited("login")
async def login_submit():
    from session.user_auth import authenticate_user

    form_data = await request.form
    email = form_data.get("email", "").strip()
    password = form_data.get("password", "")

    services = _services()
    user_id = await authenticate_user(services.movie_manager.db_pool, email, password)

    if not user_id:
        return await render_template("login.html", errors={"form": "Invalid email or password."}), 401

    state = _current_state()
    await current_app.navigation_state_store.set_user_id(state.session_id, user_id)
    state.user_id = user_id
    logger.info("User %s logged in, session %s", user_id, state.session_id)
    return redirect(url_for("main.home"), code=303)


@bp.route("/register")
async def register_page():
    if _current_user_id():
        return redirect(url_for("main.home"))
    return await render_template("register.html", errors={})


@bp.route("/register", methods=["POST"])
@csrf_required
@rate_limited("register")
async def register_submit():
    from session.user_auth import get_user_by_email, register_user, validate_registration

    form_data = await request.form
    email = form_data.get("email", "").strip()
    password = form_data.get("password", "")
    confirm_password = form_data.get("confirm_password", "")
    display_name = form_data.get("display_name", "").strip() or None

    errors = validate_registration(email, password, confirm_password)
    if errors:
        return await render_template("register.html", errors=errors), 400

    services = _services()

    # Check if email already exists
    existing = await get_user_by_email(services.movie_manager.db_pool, email)
    if existing:
        return await render_template(
            "register.html",
            errors={"email": "An account with this email already exists."},
        ), 400

    user_id = await register_user(services.movie_manager.db_pool, email, password, display_name)

    state = _current_state()
    await current_app.navigation_state_store.set_user_id(state.session_id, user_id)
    state.user_id = user_id
    logger.info("User %s registered, session %s", user_id, state.session_id)
    return redirect(url_for("main.home"), code=303)
```

- [ ] **Step 3: Update the existing logout route**

Update the existing logout route in `routes.py` to also unlink the user:

```python
@bp.route("/logout", methods=["POST"])
@csrf_required
async def logout():
    state = _current_state()
    # Unlink user from session (keep the session alive as anonymous)
    if state.user_id:
        await current_app.navigation_state_store.set_user_id(state.session_id, None)
        state.user_id = None
        logger.info("User logged out, session %s", state.session_id)

    response = redirect(url_for("main.home"), code=303)
    return response
```

Note: This changes logout from deleting the session to just unlinking the user. The session persists as anonymous.

- [ ] **Step 4: Add watched-list routes**

```python
@bp.route("/watched")
async def watched_list_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()
    page = int(request.args.get("page", 1))
    per_page = 20
    offset = (page - 1) * per_page

    movies = await services.movie_manager.watched_store.list_watched(user_id, limit=per_page, offset=offset)
    total = await services.movie_manager.watched_store.count(user_id)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return await render_template(
        "watched_list.html",
        movies=movies,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@bp.route("/watched/add/<tconst>", methods=["POST"])
@csrf_required
async def add_to_watched(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watched_store.add(user_id, tconst)
    logger.info("User %s marked %s as watched", user_id, tconst)

    referrer = request.referrer
    if referrer:
        return redirect(referrer, code=303)
    return redirect(url_for("main.movie_detail", tconst=tconst), code=303)


@bp.route("/watched/remove/<tconst>", methods=["POST"])
@csrf_required
async def remove_from_watched(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watched_store.remove(user_id, tconst)
    logger.info("User %s removed %s from watched", user_id, tconst)

    referrer = request.referrer
    if referrer:
        return redirect(referrer, code=303)
    return redirect(url_for("main.movie_detail", tconst=tconst), code=303)
```

- [ ] **Step 5: Inject user info and watched status into template context**

Update the `inject_csrf_token` context processor to also inject user info:

```python
@bp.app_context_processor
def inject_csrf_token():
    state = getattr(g, "navigation_state", None)
    user_id = getattr(state, "user_id", None) if state else None
    return {
        "csrf_token": _get_csrf_token,
        "current_user_id": user_id,
    }
```

Update `movie_detail()` to pass watched status:

```python
@bp.route("/movie/<tconst>")
@with_timeout(_REQUEST_TIMEOUT)
async def movie_detail(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")

    state = _current_state()
    user_id = _current_user_id()
    is_watched = False
    if user_id:
        is_watched = await _services().movie_manager.watched_store.is_watched(user_id, tconst)

    logger.debug(
        "Fetching movie details for tconst: %s, session_id: %s. Correlation ID: %s",
        tconst,
        state.session_id,
        g.correlation_id,
    )
    result = await _services().movie_manager.render_movie_by_tconst(
        state,
        tconst,
        template_name="movie.html",
    )
    # Inject is_watched into template context
    if isinstance(result, str):
        # Template already rendered — need to pass through render instead
        pass

    return result
```

Actually, the cleaner approach is to pass `is_watched` through the renderer. But since `render_movie_by_tconst` returns rendered HTML, we should inject via context processor. Update the context processor:

```python
@bp.app_context_processor
def inject_csrf_token():
    state = getattr(g, "navigation_state", None)
    user_id = getattr(state, "user_id", None) if state else None
    return {
        "csrf_token": _get_csrf_token,
        "current_user_id": user_id,
        "is_watched": getattr(g, "is_watched", False),
    }
```

And in `movie_detail()`, set `g.is_watched` before rendering:

```python
@bp.route("/movie/<tconst>")
@with_timeout(_REQUEST_TIMEOUT)
async def movie_detail(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")

    state = _current_state()
    user_id = _current_user_id()
    if user_id:
        g.is_watched = await _services().movie_manager.watched_store.is_watched(user_id, tconst)
    else:
        g.is_watched = False

    logger.debug(
        "Fetching movie details for tconst: %s, session_id: %s. Correlation ID: %s",
        tconst,
        state.session_id,
        g.correlation_id,
    )
    return await _services().movie_manager.render_movie_by_tconst(
        state,
        tconst,
        template_name="movie.html",
    )
```

- [ ] **Step 6: Run existing tests to check for regressions**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS (new routes don't break existing ones).

- [ ] **Step 7: Commit**

```bash
git add routes.py
git commit -m "feat: add auth routes (login, register) and watched-list routes"
```

---

## Task 9: OAuth Setup in app.py

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add OAuth client initialization to app.py**

In `app.py`, add Authlib OAuth setup. After `_init_core()` in `create_app()`, add OAuth configuration:

```python
def _init_oauth(app):
    """Phase 1b: OAuth client setup (optional — skipped if no credentials configured)."""
    from authlib.integrations.httpx_client import AsyncOAuth2Client

    google_client_id = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    apple_client_id = os.getenv("APPLE_CLIENT_ID")
    redirect_base = os.getenv("OAUTH_REDIRECT_BASE_URL", "http://127.0.0.1:5000")

    app.oauth_config = {
        "google_enabled": bool(google_client_id and google_client_secret),
        "apple_enabled": bool(apple_client_id),
        "google_client_id": google_client_id,
        "google_client_secret": google_client_secret,
        "apple_client_id": apple_client_id,
        "apple_team_id": os.getenv("APPLE_TEAM_ID"),
        "apple_key_id": os.getenv("APPLE_KEY_ID"),
        "apple_private_key": os.getenv("APPLE_PRIVATE_KEY"),
        "redirect_base": redirect_base,
    }
```

Call it in `create_app()` after `_init_core()`:

```python
    movie_manager = _init_core(app)
    _init_oauth(app)
    metrics_collector = _init_metrics(app, movie_manager)
```

- [ ] **Step 2: Add OAuth routes to routes.py**

Add Google and Apple OAuth routes. These are more involved but follow the standard Authorization Code flow:

```python
@bp.route("/auth/google")
async def auth_google():
    oauth_config = current_app.oauth_config
    if not oauth_config.get("google_enabled"):
        abort(404, "Google sign-in not configured")

    import secrets as stdlib_secrets
    state_token = stdlib_secrets.token_urlsafe(32)
    nav_state = _current_state()
    # Store OAuth state in a temporary key on the session for CSRF protection
    g.oauth_state = state_token

    redirect_uri = f"{oauth_config['redirect_base']}/auth/google/callback"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={oauth_config['google_client_id']}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=openid+email+profile"
        f"&state={state_token}"
    )
    return redirect(auth_url)


@bp.route("/auth/google/callback")
async def auth_google_callback():
    from session.user_auth import find_or_create_oauth_user, get_user_by_email

    oauth_config = current_app.oauth_config
    if not oauth_config.get("google_enabled"):
        abort(404)

    code = request.args.get("code")
    if not code:
        await flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("main.login_page"))

    import httpx

    redirect_uri = f"{oauth_config['redirect_base']}/auth/google/callback"

    async with httpx.AsyncClient() as client:
        # Exchange code for tokens
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": oauth_config["google_client_id"],
                "client_secret": oauth_config["google_client_secret"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != 200:
            await flash("Google sign-in failed. Please try again.", "error")
            return redirect(url_for("main.login_page"))

        tokens = token_response.json()

        # Get user info
        userinfo_response = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if userinfo_response.status_code != 200:
            await flash("Google sign-in failed. Please try again.", "error")
            return redirect(url_for("main.login_page"))

        userinfo = userinfo_response.json()

    email = userinfo.get("email")
    oauth_sub = userinfo.get("sub")
    display_name = userinfo.get("name")

    if not email or not oauth_sub:
        await flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("main.login_page"))

    services = _services()
    db_pool = services.movie_manager.db_pool

    # Check for email conflict with different provider
    existing = await get_user_by_email(db_pool, email)
    if existing and existing["auth_provider"] != "google":
        await flash(
            f"An account with this email already exists. Please log in with {existing['auth_provider']}.",
            "error",
        )
        return redirect(url_for("main.login_page"))

    user_id = await find_or_create_oauth_user(
        db_pool,
        provider="google",
        oauth_sub=oauth_sub,
        email=email,
        display_name=display_name,
    )

    state = _current_state()
    await current_app.navigation_state_store.set_user_id(state.session_id, user_id)
    state.user_id = user_id
    logger.info("User %s logged in via Google, session %s", user_id, state.session_id)
    return redirect(url_for("main.home"), code=303)
```

Apple OAuth follows a similar pattern but uses POST callback and JWT client secret generation. This can be added as a follow-up since Apple requires more setup (developer account, private key, etc.). For now, stub it:

```python
@bp.route("/auth/apple")
async def auth_apple():
    oauth_config = current_app.oauth_config
    if not oauth_config.get("apple_enabled"):
        abort(404, "Apple sign-in not configured")
    # Apple OAuth implementation — requires JWT client secret generation
    # This is a placeholder until Apple developer credentials are configured
    abort(501, "Apple sign-in coming soon")


@bp.route("/auth/apple/callback", methods=["POST"])
async def auth_apple_callback():
    abort(501, "Apple sign-in coming soon")
```

- [ ] **Step 3: Inject OAuth availability into templates**

Update the context processor:

```python
@bp.app_context_processor
def inject_csrf_token():
    state = getattr(g, "navigation_state", None)
    user_id = getattr(state, "user_id", None) if state else None
    oauth_config = current_app.oauth_config if hasattr(current_app, "oauth_config") else {}
    return {
        "csrf_token": _get_csrf_token,
        "current_user_id": user_id,
        "is_watched": getattr(g, "is_watched", False),
        "google_enabled": oauth_config.get("google_enabled", False),
        "apple_enabled": oauth_config.get("apple_enabled", False),
    }
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py routes.py
git commit -m "feat: add OAuth initialization and Google/Apple auth routes"
```

---

## Task 10: Templates — Login, Register, Watched List

**Files:**
- Create: `templates/login.html`
- Create: `templates/register.html`
- Create: `templates/watched_list.html`

- [ ] **Step 1: Create login.html**

Create `templates/login.html`. Follow the existing pattern from `set_filters.html` — standalone page with its own head, navbar, and styling. The template should include:

- Email input field
- Password input field
- Hidden CSRF token field
- Submit button
- Google Sign-In button (shown only if `google_enabled`)
- Apple Sign-In button (shown only if `apple_enabled`)
- Link to `/register`
- Error display for `errors.form`, `errors.email`, `errors.password`
- Form action: `POST /login`

- [ ] **Step 2: Create register.html**

Create `templates/register.html`. Similar structure to login, with:

- Email input field
- Display name input field (optional)
- Password input field
- Confirm password input field
- Hidden CSRF token field
- Submit button
- Google/Apple Sign-In buttons
- Link to `/login`
- Error display for each field
- Form action: `POST /register`

- [ ] **Step 3: Create watched_list.html**

Create `templates/watched_list.html`. Paginated grid with:

- Page title: "My Watched List"
- Card grid: each card shows poster thumbnail (from TMDb URL via slug/tconst), title, year, watched date
- "Remove" button on each card (POST form to `/watched/remove/<tconst>` with CSRF)
- Empty state message when `movies` is empty
- Pagination controls (Previous/Next, page N of M)
- Navbar include

- [ ] **Step 4: Verify templates render**

Start the dev server and manually verify each page loads:
- `GET /login` — shows login form
- `GET /register` — shows registration form
- After logging in, `GET /watched` — shows empty watched list

- [ ] **Step 5: Commit**

```bash
git add templates/login.html templates/register.html templates/watched_list.html
git commit -m "feat: add login, register, and watched list page templates"
```

---

## Task 11: Template Modifications — Navbar, Movie Card, Filters

**Files:**
- Modify: `templates/navbar_modern.html`
- Modify: `templates/movie_card.html`
- Modify: `templates/set_filters.html`

- [ ] **Step 1: Update navbar for auth state**

In `templates/navbar_modern.html`, add login/logout/user menu. In the desktop nav actions section, add:

- If `current_user_id`: show user dropdown with "My Watched List" link and a POST logout form
- If not `current_user_id`: show "Log In" link

Repeat for mobile menu section.

- [ ] **Step 2: Add "Mark as Watched" button to movie_card.html**

In `templates/movie_card.html`, after the action row (around line 97, after the external links), add a watched toggle:

```html
    {% if current_user_id %}
    <div class="mt-3">
      {% if is_watched %}
      <form method="POST" action="/watched/remove/{{ movie.tconst }}" class="inline">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <button type="submit" class="inline-flex items-center gap-1.5 rounded-full bg-green-600 px-4 py-2 text-xs font-semibold text-white hover:bg-green-700">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>
          Watched
        </button>
      </form>
      {% else %}
      <form method="POST" action="/watched/add/{{ movie.tconst }}" class="inline">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <button type="submit" class="inline-flex items-center gap-1.5 rounded-full chip px-4 py-2 text-xs font-semibold text-body hover:opacity-80">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
          Mark as Watched
        </button>
      </form>
      {% endif %}
    </div>
    {% endif %}
```

- [ ] **Step 3: Add "Exclude watched" checkbox to set_filters.html**

In `templates/set_filters.html`, add a checkbox section before the genres section (around line 213). Only show it if the user is logged in:

```html
              {% if current_user_id %}
              <!-- Exclude Watched -->
              <section class="space-y-2">
                <div class="flex items-center gap-3">
                  <input type="checkbox" id="excludeWatched" name="exclude_watched"
                         value="on"
                         {% if current_filters.get('exclude_watched', true) %}checked{% endif %}
                         class="h-4 w-4 rounded border-token text-accent focus:ring-accent" />
                  <label for="excludeWatched" class="text-sm font-medium text-primary">
                    Exclude movies I've watched
                  </label>
                </div>
              </section>
              {% endif %}
```

Also add a hidden field to indicate when the checkbox is unchecked (since unchecked checkboxes don't submit values):

```html
              {% if current_user_id %}
              <input type="hidden" name="exclude_watched" value="off">
              <!-- Then the checkbox with value="on" overrides when checked -->
              {% endif %}
```

Wait — the hidden field approach with override is correct: the hidden `value="off"` is always submitted, but when the checkbox is checked its `value="on"` also submits. The `normalize_filters` function checks for `"off"` specifically. The hidden input must come BEFORE the checkbox so the checkbox value overrides it.

- [ ] **Step 4: Verify visually**

Start dev server and check:
- Navbar shows "Log In" when logged out, user menu when logged in
- Movie card shows "Mark as Watched" button when logged in
- Filters page shows "Exclude watched" checkbox when logged in
- All hidden/visible toggles work correctly for anonymous users

- [ ] **Step 5: Commit**

```bash
git add templates/navbar_modern.html templates/movie_card.html templates/set_filters.html
git commit -m "feat: add auth UI to navbar, watched button to movie card, exclude filter to filters page"
```

---

## Task 12: Final Integration Testing and Cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 2: Run with coverage**

Run: `python3 -m pytest tests/ --cov=. --cov-report=term-missing`
Expected: Coverage above 40% (CI threshold).

- [ ] **Step 3: Lint and format**

Run: `black . --line-length 100 && flake8 . --exclude=venv,node_modules`
Expected: Clean output.

- [ ] **Step 4: Manual smoke test**

1. Start the dev server: `python3 app.py`
2. Visit `http://127.0.0.1:5000/` — verify home page loads
3. Visit `/login` — verify login form renders
4. Visit `/register` — register a test account
5. Verify navbar shows logged-in state
6. Navigate to a movie — verify "Mark as Watched" button appears
7. Click "Mark as Watched" — verify button changes to "Watched"
8. Visit `/watched` — verify movie appears in list
9. Visit `/filters` — verify "Exclude watched" checkbox is present and checked
10. Apply filters — verify watched movie is excluded from results
11. Uncheck "Exclude watched" — verify watched movie can appear again
12. Logout — verify navbar returns to anonymous state

- [ ] **Step 5: Update CLAUDE.md**

Add a section documenting the new user auth system and watched list to `CLAUDE.md` under the appropriate sections.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: complete user accounts and watched-list feature"
```

---

## Verification

### Automated
- `python3 -m pytest tests/ -v` — all tests pass
- `python3 -m pytest tests/ --cov=. --cov-report=term-missing` — coverage above 40%
- `black . --line-length 100` — code formatted
- `flake8 . --exclude=venv,node_modules` — no lint errors

### Manual
1. Anonymous browsing still works without login
2. Registration creates account and links session
3. Login finds existing account and links session
4. Logout unlinks user but keeps session
5. "Mark as Watched" button appears on movie cards for logged-in users
6. Watched movies appear in `/watched` page
7. "Exclude watched" checkbox on filters page works (default ON)
8. Watched movies are excluded from movie discovery when filter is on
9. Unchecking "Exclude watched" allows watched movies to appear again
10. Google OAuth login works when credentials are configured
