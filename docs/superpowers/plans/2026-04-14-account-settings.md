# Account Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a v1 `/account` settings surface with five tabs (Profile, Security, Preferences, Data, Danger zone) that lets authenticated users manage their profile, change passwords, revoke sessions, set viewing preferences, import from Letterboxd, export watched data, and delete their account — all styled to match the existing app design language.

**Architecture:** All account routes register on the existing shared `bp` blueprint (`nextreel/web/routes/shared.py`) via a new `nextreel/web/routes/account.py` module, wired in `nextreel/web/routes/__init__.py`. Schema changes are additive (new columns on `users`, one new `letterboxd_imports` table) and applied idempotently through `infra/runtime_schema.py`. A new `session/revocation.py` primitive backs password-change, "sign out everywhere," and account deletion. Letterboxd imports use arq for background work and poll a status endpoint. A new `.account-*` CSS namespace extends `static/css/input.css`, reusing the existing `.auth-*` form classes and CSS variables so themes and typography match the rest of the app.

**Tech Stack:** Python 3.11, Quart, MySQL-backed runtime schema, Redis-backed sessions + cache, arq, pytest-asyncio, AsyncMock, Tailwind (via `static/css/input.css` component classes).

**Spec:** `docs/superpowers/specs/2026-04-14-account-settings-design.md`

---

## File Map

### New files

- `nextreel/web/routes/account.py` — all `/account/*` route handlers, registered on shared `bp`
- `session/revocation.py` — `revoke_user_sessions(redis, user_id, except_session_id)` primitive
- `templates/account/layout.html` — tab shell (head, navbar, tab bar, panel slot)
- `templates/account/profile.html` — Profile panel
- `templates/account/security.html` — Security panel
- `templates/account/preferences.html` — Preferences panel
- `templates/account/data.html` — Data panel
- `templates/account/danger.html` — Danger zone panel
- `templates/account/import_progress.html` — Letterboxd import progress page
- `tests/session/test_revocation.py` — unit tests for the revocation primitive
- `tests/web/test_account_routes.py` — route-level tests for all `/account/*` endpoints
- `tests/workers/test_letterboxd_import_job.py` — arq job tests

### Modified files

- `infra/runtime_schema.py` — add `default_filters_json` + `theme_preference` columns on `users`, add `letterboxd_imports` table and its ensure helper
- `session/user_preferences.py` — add helpers for `theme_preference` and `default_filters_json`
- `nextreel/web/routes/__init__.py` — import account handlers so they register on `bp`
- `nextreel/workers/worker.py` — register `import_letterboxd` arq job
- `static/css/input.css` — add `.account-*` namespace, `.btn-danger`, `.modal` primitive
- `templates/macros.html` — add `user_avatar(user, size)` macro
- `templates/navbar_modern.html` — avatar dropdown when logged in, mobile Account link
- `templates/set_filters.html` — add "Save as default" button
- `templates/login.html`, `templates/movie.html`, `templates/home.html`, `templates/watched_list.html`, `templates/set_filters.html` — update pre-paint theme script to read `data-theme-server` as fallback

---

## Phase 1: Schema

### Task 1: Add `theme_preference` and `default_filters_json` columns on `users`

**Files:**
- Modify: `infra/runtime_schema.py`
- Test: `tests/infra/test_runtime_schema.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/infra/test_runtime_schema.py`:

```python
async def test_ensure_users_theme_preference_column_adds_when_missing(mock_db_pool):
    from infra.runtime_schema import ensure_users_theme_preference_column

    await ensure_users_theme_preference_column(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    alter_sql = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ALTER TABLE users" in alter_sql
    assert "theme_preference" in alter_sql
    assert "VARCHAR(10)" in alter_sql


async def test_ensure_users_default_filters_json_column_adds_when_missing(mock_db_pool):
    from infra.runtime_schema import ensure_users_default_filters_json_column

    await ensure_users_default_filters_json_column(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    alter_sql = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ALTER TABLE users" in alter_sql
    assert "default_filters_json" in alter_sql
    assert "JSON" in alter_sql
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py -k "theme_preference or default_filters_json" -v`
Expected: FAIL — `ImportError: cannot import name 'ensure_users_theme_preference_column'`

- [ ] **Step 3: Implement the ensure helpers**

Add to `infra/runtime_schema.py` after `ensure_users_exclude_watched_default_column`:

```python
async def ensure_users_theme_preference_column(db_pool) -> None:
    """Add the theme preference column to existing users."""
    await _ensure_column(
        db_pool,
        "users",
        "theme_preference",
        """
        ALTER TABLE users
        ADD COLUMN theme_preference VARCHAR(10) NULL
        """,
    )


async def ensure_users_default_filters_json_column(db_pool) -> None:
    """Add the default filter presets column to existing users."""
    await _ensure_column(
        db_pool,
        "users",
        "default_filters_json",
        """
        ALTER TABLE users
        ADD COLUMN default_filters_json JSON NULL
        """,
    )
```

Then call both from `ensure_runtime_schema`, after the existing `ensure_users_exclude_watched_default_column(db_pool)` call:

```python
    await ensure_users_exclude_watched_default_column(db_pool)
    await ensure_users_theme_preference_column(db_pool)
    await ensure_users_default_filters_json_column(db_pool)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py -k "theme_preference or default_filters_json" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/runtime_schema.py tests/infra/test_runtime_schema.py
git commit -m "feat(schema): add theme_preference and default_filters_json columns on users"
```

---

### Task 2: Add `letterboxd_imports` table

**Files:**
- Modify: `infra/runtime_schema.py`
- Test: `tests/infra/test_runtime_schema.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/infra/test_runtime_schema.py`:

```python
async def test_ensure_runtime_schema_creates_letterboxd_imports_table(mock_db_pool):
    from infra.runtime_schema import _RUNTIME_SCHEMA_STATEMENTS

    matches = [s for s in _RUNTIME_SCHEMA_STATEMENTS
               if "CREATE TABLE IF NOT EXISTS letterboxd_imports" in s]
    assert len(matches) == 1
    sql = matches[0]
    for col in ("import_id", "user_id", "status", "total_rows",
                "processed", "matched", "skipped", "failed",
                "error_message", "created_at", "updated_at", "completed_at"):
        assert col in sql, f"missing column {col} in letterboxd_imports DDL"
    assert "PRIMARY KEY (import_id)" in sql or "import_id     CHAR(32) PRIMARY KEY" in sql
    assert "idx_letterboxd_user_created" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py::test_ensure_runtime_schema_creates_letterboxd_imports_table -v`
Expected: FAIL — table not defined

- [ ] **Step 3: Add the CREATE TABLE statement**

Append to the `_RUNTIME_SCHEMA_STATEMENTS` tuple in `infra/runtime_schema.py`, after the `user_watched_movies` block:

```python
    """
    CREATE TABLE IF NOT EXISTS letterboxd_imports (
        import_id     CHAR(32) PRIMARY KEY,
        user_id       CHAR(32) NOT NULL,
        status        VARCHAR(16) NOT NULL,
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py::test_ensure_runtime_schema_creates_letterboxd_imports_table -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/runtime_schema.py tests/infra/test_runtime_schema.py
git commit -m "feat(schema): add letterboxd_imports table for async import tracking"
```

---

## Phase 2: Session Revocation Primitive & Preference Helpers

### Task 3: Create `session/revocation.py`

**Files:**
- Create: `session/revocation.py`
- Test: `tests/session/test_revocation.py`

- [ ] **Step 1: Write failing tests**

Create `tests/session/test_revocation.py`:

```python
import json
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def fake_redis():
    client = AsyncMock()
    # Each call to scan returns (cursor, keys); terminate with cursor=0.
    return client


async def test_revokes_sessions_for_matching_user(fake_redis):
    from session.revocation import revoke_user_sessions

    keys_batch = [b"quart-session:sid-a", b"quart-session:sid-b", b"quart-session:sid-c"]
    fake_redis.scan.side_effect = [(0, keys_batch)]
    fake_redis.get.side_effect = [
        json.dumps({"user_id": "u1"}).encode(),
        json.dumps({"user_id": "u2"}).encode(),
        json.dumps({"user_id": "u1"}).encode(),
    ]

    count = await revoke_user_sessions(fake_redis, "u1", except_session_id="sid-a")

    assert count == 1
    deleted_keys = [call.args[0] for call in fake_redis.delete.await_args_list]
    assert b"quart-session:sid-c" in deleted_keys
    assert b"quart-session:sid-a" not in deleted_keys
    assert b"quart-session:sid-b" not in deleted_keys


async def test_revokes_all_when_except_is_none(fake_redis):
    from session.revocation import revoke_user_sessions

    fake_redis.scan.side_effect = [(0, [b"quart-session:sid-a", b"quart-session:sid-b"])]
    fake_redis.get.side_effect = [
        json.dumps({"user_id": "u1"}).encode(),
        json.dumps({"user_id": "u1"}).encode(),
    ]

    count = await revoke_user_sessions(fake_redis, "u1", except_session_id=None)

    assert count == 2
    assert fake_redis.delete.await_count == 2


async def test_skips_sessions_for_other_users(fake_redis):
    from session.revocation import revoke_user_sessions

    fake_redis.scan.side_effect = [(0, [b"quart-session:sid-x"])]
    fake_redis.get.side_effect = [json.dumps({"user_id": "other"}).encode()]

    count = await revoke_user_sessions(fake_redis, "u1", except_session_id=None)

    assert count == 0
    fake_redis.delete.assert_not_awaited()


async def test_tolerates_unparseable_session_values(fake_redis):
    from session.revocation import revoke_user_sessions

    fake_redis.scan.side_effect = [(0, [b"quart-session:bad"])]
    fake_redis.get.side_effect = [b"not-json-at-all"]

    count = await revoke_user_sessions(fake_redis, "u1", except_session_id=None)

    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/session/test_revocation.py -v`
Expected: FAIL — `ModuleNotFoundError: session.revocation`

- [ ] **Step 3: Implement the primitive**

Create `session/revocation.py`:

```python
"""Bulk session revocation for a given user.

Used by:
- Password change success (revoke all except current)
- Explicit "Sign out everywhere" button (revoke all except current)
- Account deletion (revoke all including current)
"""

from __future__ import annotations

import json

from logging_config import get_logger

logger = get_logger(__name__)

_SESSION_KEY_PATTERN = b"quart-session:*"
_KEY_PREFIX = b"quart-session:"


async def revoke_user_sessions(
    redis_client,
    user_id: str,
    *,
    except_session_id: str | None = None,
) -> int:
    """Delete every quart-session entry whose stored user_id matches.

    Returns the number of sessions revoked. Silently tolerates malformed
    session payloads (treated as non-matches so a poison entry cannot
    block revocation for well-formed ones).
    """
    cursor: int = 0
    revoked = 0
    except_suffix = (
        ("quart-session:" + except_session_id).encode()
        if except_session_id
        else None
    )

    while True:
        cursor, keys = await redis_client.scan(
            cursor=cursor, match=_SESSION_KEY_PATTERN, count=500
        )
        for key in keys:
            if except_suffix is not None and key == except_suffix:
                continue
            payload = await redis_client.get(key)
            if payload is None:
                continue
            try:
                data = json.loads(payload)
            except (ValueError, TypeError):
                logger.debug("Skipping unparseable session key %r", key)
                continue
            if data.get("user_id") == user_id:
                await redis_client.delete(key)
                revoked += 1
        if cursor == 0:
            break

    logger.info("Revoked %d sessions for user=%s", revoked, user_id)
    return revoked
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/session/test_revocation.py -v`
Expected: PASS (all four tests)

- [ ] **Step 5: Commit**

```bash
git add session/revocation.py tests/session/test_revocation.py
git commit -m "feat(session): add revoke_user_sessions primitive"
```

---

### Task 4: Extend `session/user_preferences.py` with theme and default-filter helpers

**Files:**
- Modify: `session/user_preferences.py`
- Test: `tests/session/test_user_preferences.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/session/test_user_preferences.py`:

```python
import json
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def db_pool():
    pool = AsyncMock()
    return pool


async def test_get_theme_preference_returns_value(db_pool):
    from session.user_preferences import get_theme_preference

    db_pool.execute.return_value = {"theme_preference": "dark"}
    assert await get_theme_preference(db_pool, "u1") == "dark"


async def test_get_theme_preference_returns_none_when_unset(db_pool):
    from session.user_preferences import get_theme_preference

    db_pool.execute.return_value = {"theme_preference": None}
    assert await get_theme_preference(db_pool, "u1") is None


async def test_set_theme_preference_rejects_unknown_value(db_pool):
    from session.user_preferences import set_theme_preference

    with pytest.raises(ValueError):
        await set_theme_preference(db_pool, "u1", "rainbow")


async def test_set_theme_preference_writes_valid_value(db_pool):
    from session.user_preferences import set_theme_preference

    await set_theme_preference(db_pool, "u1", "light")
    db_pool.execute.assert_awaited_once()
    sql = db_pool.execute.await_args.args[0]
    params = db_pool.execute.await_args.args[1]
    assert "UPDATE users" in sql
    assert params[0] == "light"
    assert params[-1] == "u1"


async def test_set_theme_preference_accepts_none(db_pool):
    from session.user_preferences import set_theme_preference

    await set_theme_preference(db_pool, "u1", None)
    params = db_pool.execute.await_args.args[1]
    assert params[0] is None


async def test_get_default_filters_returns_parsed_dict(db_pool):
    from session.user_preferences import get_default_filters

    db_pool.execute.return_value = {
        "default_filters_json": json.dumps({"genres": ["Horror"], "min_year": 2000})
    }
    result = await get_default_filters(db_pool, "u1")
    assert result == {"genres": ["Horror"], "min_year": 2000}


async def test_get_default_filters_returns_none_when_unset(db_pool):
    from session.user_preferences import get_default_filters

    db_pool.execute.return_value = {"default_filters_json": None}
    assert await get_default_filters(db_pool, "u1") is None


async def test_set_default_filters_serializes_to_json(db_pool):
    from session.user_preferences import set_default_filters

    payload = {"genres": ["Horror", "Thriller"], "min_rating": 7.0}
    await set_default_filters(db_pool, "u1", payload)
    params = db_pool.execute.await_args.args[1]
    assert json.loads(params[0]) == payload


async def test_clear_default_filters_writes_null(db_pool):
    from session.user_preferences import clear_default_filters

    await clear_default_filters(db_pool, "u1")
    params = db_pool.execute.await_args.args[1]
    assert params[0] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/session/test_user_preferences.py -v`
Expected: FAIL on the new tests (existing tests still pass)

- [ ] **Step 3: Implement the helpers**

Append to `session/user_preferences.py`:

```python
import json

_VALID_THEMES = frozenset({"light", "dark", "system"})


async def get_theme_preference(db_pool, user_id: str) -> str | None:
    row = await db_pool.execute(
        "SELECT theme_preference FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )
    if not row:
        return None
    value = row.get("theme_preference")
    return value if value in _VALID_THEMES else None


async def set_theme_preference(db_pool, user_id: str, value: str | None) -> None:
    if value is not None and value not in _VALID_THEMES:
        raise ValueError(f"Invalid theme preference: {value!r}")
    await db_pool.execute(
        """
        UPDATE users
        SET theme_preference = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [value, utcnow(), user_id],
        fetch="none",
    )


async def get_default_filters(db_pool, user_id: str) -> dict | None:
    row = await db_pool.execute(
        "SELECT default_filters_json FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )
    if not row or not row.get("default_filters_json"):
        return None
    raw = row["default_filters_json"]
    if isinstance(raw, (dict, list)):
        return raw if isinstance(raw, dict) else None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def set_default_filters(db_pool, user_id: str, filters: dict) -> None:
    await db_pool.execute(
        """
        UPDATE users
        SET default_filters_json = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [json.dumps(filters), utcnow(), user_id],
        fetch="none",
    )


async def clear_default_filters(db_pool, user_id: str) -> None:
    await db_pool.execute(
        """
        UPDATE users
        SET default_filters_json = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [None, utcnow(), user_id],
        fetch="none",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/session/test_user_preferences.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add session/user_preferences.py tests/session/test_user_preferences.py
git commit -m "feat(preferences): add theme and default-filter helpers"
```

---

## Phase 3: CSS and Macros

### Task 5: Add `.account-*` namespace, `.btn-danger`, and modal primitives

**Files:**
- Modify: `static/css/input.css`

- [ ] **Step 1: Add styles**

Append to `static/css/input.css` (inside the `@layer components { ... }` block if one exists, otherwise at the end):

```css
/* =========================
   Account settings surface
   ========================= */

.account-page {
  max-width: 900px;
  margin: 0 auto;
  padding: 2rem 1.25rem 4rem;
}

.account-tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 2rem;
  overflow-x: auto;
}

.account-tab {
  padding: 0.75rem 1rem;
  color: var(--color-muted);
  text-decoration: none;
  border-bottom: 2px solid transparent;
  font-weight: 500;
  white-space: nowrap;
}
.account-tab:hover { color: var(--color-text); }

.account-tab-active {
  color: var(--color-text);
  border-bottom-color: var(--color-accent);
  font-weight: 600;
}

.account-tab-danger { color: #dc2626; }
.account-tab-danger.account-tab-active { border-bottom-color: #dc2626; }

.account-panel { display: flex; flex-direction: column; gap: 1.5rem; }

.account-section-title {
  font-family: var(--font-serif, 'Merriweather', Georgia, serif);
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0 0 0.25rem;
}

.account-section-description {
  color: var(--color-muted);
  margin: 0 0 1rem;
  font-size: 0.9375rem;
}

.account-card {
  border: 1px solid var(--color-border);
  border-radius: 0.75rem;
  padding: 1.25rem 1.5rem;
  background: var(--color-surface, transparent);
}

.account-card + .account-card { margin-top: 1rem; }

.account-card-title {
  font-size: 1.125rem;
  font-weight: 600;
  margin: 0 0 0.5rem;
}

.account-danger-card {
  border-color: #dc2626;
  border-left-width: 4px;
}

.account-field { display: flex; flex-direction: column; gap: 0.375rem; margin-bottom: 1rem; }
.account-field-readonly { color: var(--color-muted); font-size: 0.9375rem; }

.account-field-row {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1rem;
}
@media (min-width: 640px) {
  .account-field-row { grid-template-columns: 1fr 1fr; }
}

.account-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  font-weight: 600;
  color: white;
  letter-spacing: 0.03em;
  user-select: none;
}
.account-avatar-sm { width: 2rem;   height: 2rem;   font-size: 0.8125rem; }
.account-avatar-md { width: 2.5rem; height: 2.5rem; font-size: 0.9375rem; }
.account-avatar-lg { width: 4.5rem; height: 4.5rem; font-size: 1.5rem;    }

.account-avatar-dropdown { position: relative; }
.account-avatar-dropdown-trigger {
  background: none; border: 0; padding: 0; cursor: pointer;
  display: inline-flex;
}
.account-avatar-dropdown-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 0.5rem);
  min-width: 11rem;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 0.5rem;
  box-shadow: 0 10px 24px rgba(0,0,0,0.15);
  display: none;
  z-index: 40;
}
.account-avatar-dropdown-menu.open { display: block; }
.account-avatar-dropdown-menu a,
.account-avatar-dropdown-menu button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.625rem 0.875rem;
  color: var(--color-text);
  background: none;
  border: 0;
  cursor: pointer;
  font: inherit;
  text-decoration: none;
}
.account-avatar-dropdown-menu a:hover,
.account-avatar-dropdown-menu button:hover {
  color: var(--color-accent);
  background: rgba(0,0,0,0.04);
}

/* =========================
   Danger button variant
   ========================= */
.btn-danger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.625rem 1.125rem;
  font-weight: 600;
  border-radius: 0.5rem;
  background: #dc2626;
  color: white;
  border: 0;
  cursor: pointer;
}
.btn-danger:hover { background: #b91c1c; }
.btn-danger:disabled { opacity: 0.55; cursor: not-allowed; }

/* =========================
   Modal primitive
   ========================= */
.modal-backdrop {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.55);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: 1rem;
}
.modal-backdrop.open { display: flex; }
.modal-panel {
  background: var(--color-bg);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: 0.75rem;
  padding: 1.5rem;
  width: 100%;
  max-width: 28rem;
  box-shadow: 0 30px 60px rgba(0,0,0,0.35);
}
.modal-panel h3 { margin: 0 0 0.5rem; font-size: 1.125rem; font-weight: 600; }
.modal-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1.25rem; }
```

- [ ] **Step 2: Rebuild CSS and visually spot-check**

Run: `npm run build-css`
Expected: exits 0

Open `static/css/output.css` and confirm the `.account-page`, `.btn-danger`, and `.modal-backdrop` classes appear.

- [ ] **Step 3: Commit**

```bash
git add static/css/input.css static/css/output.css
git commit -m "feat(css): add .account-* namespace, .btn-danger, .modal primitives"
```

---

### Task 6: Add `user_avatar` macro

**Files:**
- Modify: `templates/macros.html`

- [ ] **Step 1: Add the macro**

Append to `templates/macros.html`:

```jinja
{% macro user_avatar(user, size='md') -%}
  {%- set name = (user.display_name or user.email.split('@')[0]) | trim -%}
  {%- set words = name.split() -%}
  {%- if words | length >= 2 -%}
    {%- set initials = (words[0][:1] ~ words[-1][:1]) | upper -%}
  {%- else -%}
    {%- set initials = (name[:2]) | upper -%}
  {%- endif -%}
  {%- set palette = [
    '#6366f1','#8b5cf6','#ec4899','#f97316',
    '#eab308','#22c55e','#14b8a6','#0ea5e9'
  ] -%}
  {%- set seed = user.user_id | default('') -%}
  {%- set bucket = 0 -%}
  {%- for ch in seed -%}{%- set bucket = bucket + ch | ord -%}{%- endfor -%}
  {%- set color = palette[bucket % palette | length] -%}
  <span class="account-avatar account-avatar-{{ size }}" style="background:{{ color }};" aria-hidden="true">{{ initials }}</span>
{%- endmacro %}
```

Note: Jinja doesn't expose `ord` by default. Work around this by exposing `ord` as a filter in the app. Check `nextreel/web/app.py` for existing filter registration; if none exists, add one:

```python
app.jinja_env.filters.setdefault("ord", lambda s: ord(s) if s else 0)
```

If that's awkward, replace the ord-loop with a Python function call — make the macro a thin wrapper over a context processor that returns a `{initials, color}` dict. Simpler alternative: register `user_avatar_data(user)` as a context function and let Jinja render just the markup. Use whichever fits the existing app conventions; add it to `shared.py` alongside `_current_user_id`.

- [ ] **Step 2: Verify the app still boots**

Run: `python3 -c "from nextreel.web.app import create_app; import asyncio; asyncio.run(create_app())" 2>&1 | head -20`
Expected: no import errors related to the macro.

- [ ] **Step 3: Commit**

```bash
git add templates/macros.html nextreel/web/app.py nextreel/web/routes/shared.py
git commit -m "feat(templates): add user_avatar macro for initials circle"
```

---

## Phase 4: Blueprint Scaffolding

### Task 7: Create `account.py` with auth guard + tab router

**Files:**
- Create: `nextreel/web/routes/account.py`
- Create: `templates/account/layout.html`
- Modify: `nextreel/web/routes/__init__.py`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/web/test_account_routes.py`:

```python
import pytest
from quart import session


async def test_account_redirects_unauthenticated_to_login(client):
    response = await client.get("/account", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "/login" in response.headers["Location"]


async def test_account_logged_in_redirects_to_profile_tab(client, logged_in_user):
    response = await client.get("/account", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "tab=profile" in response.headers["Location"]


async def test_account_unknown_tab_falls_back_to_profile(client, logged_in_user):
    response = await client.get("/account?tab=bogus", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "tab=profile" in response.headers["Location"]


async def test_account_profile_tab_renders(client, logged_in_user):
    response = await client.get("/account?tab=profile")
    assert response.status_code == 200
    body = (await response.get_data()).decode()
    assert "Profile" in body
    assert logged_in_user["email"] in body
```

The `logged_in_user` fixture should follow the pattern of existing auth-related tests (look at `tests/web/test_auth_routes.py` for the convention).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_account_routes.py -v`
Expected: FAIL — route not registered

- [ ] **Step 3: Create the layout template**

Create `templates/account/layout.html`:

```jinja
{% from "macros.html" import user_avatar with context %}
<!DOCTYPE html>
<html lang="en" class="scroll-smooth" {% if server_theme %}data-theme-server="{{ server_theme }}"{% endif %}>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ page_title or 'Account' }} – Nextreel</title>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Merriweather:ital,wght@0,300;0,400;0,700;1,400&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/output.css') }}?v={{ config.get('CSS_VERSION', '1') }}">
  <script>
    (() => {
      try {
        const pref = localStorage.getItem('nr-theme');
        if (pref === 'light' || pref === 'dark') {
          document.documentElement.setAttribute('data-theme', pref);
          return;
        }
        const server = document.documentElement.getAttribute('data-theme-server');
        if (server === 'light' || server === 'dark') {
          document.documentElement.setAttribute('data-theme', server);
        }
      } catch (e) {}
    })();
  </script>
  <style> body { font-family: var(--font-sans, 'DM Sans', system-ui, sans-serif); } </style>
</head>
<body class="antialiased">
  <a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow">Skip to content</a>
  {% include 'navbar_modern.html' %}

  <main id="main" class="account-page">
    <h1 class="account-section-title" style="margin-bottom:1rem;">Account</h1>

    <nav class="account-tabs" aria-label="Account sections">
      {% set tabs = [
        ('profile', 'Profile', false),
        ('security', 'Security', false),
        ('preferences', 'Preferences', false),
        ('data', 'Data', false),
        ('danger', 'Danger zone', true),
      ] %}
      {% for key, label, danger in tabs %}
        <a href="{{ url_for('main.account_view') }}?tab={{ key }}"
           class="account-tab{% if active_tab == key %} account-tab-active{% endif %}{% if danger %} account-tab-danger{% endif %}">{{ label }}</a>
      {% endfor %}
    </nav>

    <section class="account-panel" aria-live="polite">
      {% block panel %}{% endblock %}
    </section>
  </main>

  {% include 'footer_modern.html' %}
</body>
</html>
```

- [ ] **Step 4: Create `nextreel/web/routes/account.py`**

Create `nextreel/web/routes/account.py`:

```python
"""Account settings routes — Profile, Security, Preferences, Data, Danger zone."""

from __future__ import annotations

from quart import abort, current_app, g, redirect, render_template, request, session, url_for

from infra.route_helpers import csrf_required, rate_limited
from logging_config import get_logger
from nextreel.web.routes.shared import (
    _current_user_id,
    bp,
)
from session import user_preferences
from session.user_auth import get_user_by_id

logger = get_logger(__name__)

_VALID_TABS = ("profile", "security", "preferences", "data", "danger")


def _require_user() -> str:
    user_id = _current_user_id()
    if not user_id:
        # Preserve the original tab in next so post-login returns here.
        target = request.full_path if request.query_string else request.path
        abort(redirect(url_for("main.login_page", next=target)))
    return user_id


@bp.route("/account")
async def account_view():
    if not _current_user_id():
        target = "/account?tab=profile"
        return redirect(url_for("main.login_page", next=target))

    tab = request.args.get("tab", "profile")
    if tab not in _VALID_TABS:
        return redirect(url_for("main.account_view") + "?tab=profile")

    db_pool = current_app.config["DB_POOL"]
    user_id = _current_user_id()
    user = await get_user_by_id(db_pool, user_id)
    if not user:
        session.clear()
        return redirect(url_for("main.login_page"))

    exclude_watched_default = await user_preferences.get_exclude_watched_default(db_pool, user_id)
    theme_preference = await user_preferences.get_theme_preference(db_pool, user_id)
    default_filters = await user_preferences.get_default_filters(db_pool, user_id)

    template = f"account/{tab}.html"
    return await render_template(
        template,
        active_tab=tab,
        user=user,
        server_theme=theme_preference,
        exclude_watched_default=exclude_watched_default,
        default_filters=default_filters,
        page_title=tab.title(),
    )
```

- [ ] **Step 5: Wire into the routes package**

Edit `nextreel/web/routes/__init__.py` to import from the new module. Add near the other module imports:

```python
from nextreel.web.routes.account import account_view
```

And add `"account_view"` to `__all__`.

- [ ] **Step 6: Create a minimal profile.html so the render succeeds**

Create `templates/account/profile.html`:

```jinja
{% extends "account/layout.html" %}
{% block panel %}
  <div class="account-card">
    <h2 class="account-card-title">Profile</h2>
    <p class="account-field-readonly">{{ user.email }}</p>
  </div>
{% endblock %}
```

Create the same shell for the other four tabs so the tab-router works:

```jinja
{# templates/account/security.html #}
{% extends "account/layout.html" %}
{% block panel %}<div class="account-card"><h2 class="account-card-title">Security</h2></div>{% endblock %}
```

Repeat for `preferences.html`, `data.html`, `danger.html` (each with a matching `account-card-title`).

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest tests/web/test_account_routes.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add nextreel/web/routes/account.py nextreel/web/routes/__init__.py templates/account/ tests/web/test_account_routes.py
git commit -m "feat(account): tab router + blueprint scaffolding for /account"
```

---

## Phase 5: Profile Tab

### Task 8: Profile panel render + save

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Modify: `templates/account/profile.html`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/web/test_account_routes.py`:

```python
async def test_profile_post_updates_display_name(client, logged_in_user, db_pool):
    response = await client.post(
        "/account/profile",
        form={"csrf_token": logged_in_user["csrf"], "display_name": "Bryce H."},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    row = await db_pool.execute(
        "SELECT display_name FROM users WHERE user_id = %s",
        [logged_in_user["user_id"]],
        fetch="one",
    )
    assert row["display_name"] == "Bryce H."


async def test_profile_post_rejects_overlong_name(client, logged_in_user):
    response = await client.post(
        "/account/profile",
        form={"csrf_token": logged_in_user["csrf"], "display_name": "x" * 101},
    )
    # Either a 400 or a render with an error flash — both are acceptable.
    body = (await response.get_data()).decode().lower()
    assert response.status_code == 400 or "too long" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_account_routes.py -k profile -v`
Expected: FAIL

- [ ] **Step 3: Implement the POST handler**

Add to `nextreel/web/routes/account.py`:

```python
from infra.time_utils import utcnow

MAX_DISPLAY_NAME_LENGTH = 100


@bp.route("/account/profile", methods=["POST"])
@csrf_required
async def account_profile_save():
    user_id = _require_user()
    form = await request.form
    raw = (form.get("display_name") or "").strip()
    if len(raw) > MAX_DISPLAY_NAME_LENGTH:
        abort(400, description="Display name too long")
    display_name = raw or None

    db_pool = current_app.config["DB_POOL"]
    await db_pool.execute(
        "UPDATE users SET display_name = %s, updated_at = %s WHERE user_id = %s",
        [display_name, utcnow(), user_id],
        fetch="none",
    )
    logger.info("Account action: %s user=%s", "profile_save", user_id)
    return redirect(url_for("main.account_view") + "?tab=profile")
```

- [ ] **Step 4: Flesh out the profile template**

Replace `templates/account/profile.html`:

```jinja
{% extends "account/layout.html" %}
{% from "macros.html" import user_avatar with context %}
{% block panel %}
  <div class="account-card">
    <h2 class="account-card-title">Profile</h2>
    <p class="account-section-description">How your account appears in Nextreel.</p>

    <div style="display:flex; align-items:center; gap:1rem; margin-bottom:1.5rem;">
      {{ user_avatar(user, 'lg') }}
      <div>
        <div style="font-weight:600; font-size:1.125rem;">
          {{ user.display_name or user.email.split('@')[0] }}
        </div>
        <div class="account-field-readonly">Member since
          {{ user.created_at.strftime('%B %Y') if user.created_at else '—' }}
        </div>
      </div>
    </div>

    <form method="POST" action="{{ url_for('main.account_profile_save') }}" class="space-y-4">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

      <div class="account-field">
        <label for="display_name" class="auth-input-label">Display name</label>
        <input id="display_name" name="display_name" type="text" maxlength="100"
               value="{{ user.display_name or '' }}" class="auth-input">
      </div>

      <div class="account-field">
        <label class="auth-input-label">Email</label>
        <p class="account-field-readonly">{{ user.email }} — contact support to change.</p>
      </div>

      <div class="account-field">
        <label class="auth-input-label">Signed in with</label>
        <p class="account-field-readonly">{{ user.auth_provider | capitalize }}</p>
      </div>

      <div class="pt-2">
        <button type="submit" class="auth-submit">Save</button>
      </div>
    </form>
  </div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/web/test_account_routes.py -k profile -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add nextreel/web/routes/account.py templates/account/profile.html tests/web/test_account_routes.py
git commit -m "feat(account): Profile tab — render + display-name save"
```

---

## Phase 6: Security Tab

### Task 9: Password change endpoint

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Modify: `templates/account/security.html`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/web/test_account_routes.py`:

```python
async def test_password_change_success_revokes_other_sessions(
    client, logged_in_email_user, db_pool, mock_redis
):
    response = await client.post(
        "/account/password",
        form={
            "csrf_token": logged_in_email_user["csrf"],
            "current_password": logged_in_email_user["password"],
            "new_password": "newpass12345",
            "confirm_password": "newpass12345",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    # Password actually changed
    from session.user_auth import authenticate_user
    assert await authenticate_user(db_pool, logged_in_email_user["email"],
                                   "newpass12345") is not None

    # Revocation was invoked — should touch SCAN at least once
    assert mock_redis.scan.await_count >= 1


async def test_password_change_rejects_wrong_current(client, logged_in_email_user):
    response = await client.post(
        "/account/password",
        form={
            "csrf_token": logged_in_email_user["csrf"],
            "current_password": "wrongpass",
            "new_password": "whatever12345",
            "confirm_password": "whatever12345",
        },
    )
    body = (await response.get_data()).decode().lower()
    assert "current password" in body or response.status_code == 400


async def test_password_change_rejects_mismatched_confirmation(client, logged_in_email_user):
    response = await client.post(
        "/account/password",
        form={
            "csrf_token": logged_in_email_user["csrf"],
            "current_password": logged_in_email_user["password"],
            "new_password": "newpass12345",
            "confirm_password": "different12345",
        },
    )
    body = (await response.get_data()).decode().lower()
    assert "match" in body or response.status_code == 400


async def test_password_change_rejects_short(client, logged_in_email_user):
    response = await client.post(
        "/account/password",
        form={
            "csrf_token": logged_in_email_user["csrf"],
            "current_password": logged_in_email_user["password"],
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    body = (await response.get_data()).decode().lower()
    assert "at least" in body or response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_account_routes.py -k password -v`
Expected: FAIL

- [ ] **Step 3: Implement the handler**

Add to `nextreel/web/routes/account.py`:

```python
from session.revocation import revoke_user_sessions
from session.user_auth import (
    MIN_PASSWORD_LENGTH,
    hash_password_async,
    verify_password_async,
)


@bp.route("/account/password", methods=["POST"])
@csrf_required
@rate_limited("account_password")
async def account_password_change():
    user_id = _require_user()
    form = await request.form
    current = form.get("current_password", "")
    new = form.get("new_password", "")
    confirm = form.get("confirm_password", "")

    errors: dict[str, str] = {}
    if len(new) < MIN_PASSWORD_LENGTH:
        errors["new_password"] = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if new != confirm:
        errors["confirm_password"] = "Passwords do not match."

    db_pool = current_app.config["DB_POOL"]
    row = await db_pool.execute(
        "SELECT password_hash FROM users WHERE user_id = %s AND auth_provider = 'email'",
        [user_id],
        fetch="one",
    )
    if not row or not row.get("password_hash"):
        abort(400, description="Password change is only available for email accounts.")

    if not await verify_password_async(current, row["password_hash"]):
        errors["current_password"] = "Current password is incorrect."

    if errors:
        user = await get_user_by_id(db_pool, user_id)
        return await render_template(
            "account/security.html",
            active_tab="security",
            user=user,
            server_theme=await user_preferences.get_theme_preference(db_pool, user_id),
            errors=errors,
        ), 400

    new_hash = await hash_password_async(new)
    await db_pool.execute(
        "UPDATE users SET password_hash = %s, updated_at = %s WHERE user_id = %s",
        [new_hash, utcnow(), user_id],
        fetch="none",
    )

    redis_client = current_app.config.get("REDIS_CLIENT")
    current_sid = session.sid if hasattr(session, "sid") else session.get("_id")
    if redis_client is not None:
        await revoke_user_sessions(redis_client, user_id, except_session_id=current_sid)

    logger.info("Account action: %s user=%s", "password_change", user_id)
    return redirect(url_for("main.account_view") + "?tab=security")
```

- [ ] **Step 4: Render the security template**

Replace `templates/account/security.html`:

```jinja
{% extends "account/layout.html" %}
{% block panel %}
  {% if user.auth_provider == 'email' %}
    <div class="account-card">
      <h2 class="account-card-title">Password</h2>
      <p class="account-section-description">
        Changing your password signs you out of all other devices.
      </p>
      <form method="POST" action="{{ url_for('main.account_password_change') }}" class="space-y-4">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        {% for field, label, autocomplete in [
            ('current_password', 'Current password', 'current-password'),
            ('new_password', 'New password', 'new-password'),
            ('confirm_password', 'Confirm new password', 'new-password')
        ] %}
          <div class="account-field">
            <label for="{{ field }}" class="auth-input-label">{{ label }}</label>
            <input id="{{ field }}" name="{{ field }}" type="password" required
                   autocomplete="{{ autocomplete }}"
                   class="auth-input{% if errors and errors[field] %} auth-input-error{% endif %}">
            {% if errors and errors[field] %}
              <p class="auth-field-error">{{ errors[field] }}</p>
            {% endif %}
          </div>
        {% endfor %}
        <button type="submit" class="auth-submit">Update password</button>
      </form>
    </div>
  {% else %}
    <div class="account-card">
      <h2 class="account-card-title">Password</h2>
      <p class="account-section-description">
        Your password is managed by your {{ user.auth_provider | capitalize }} account.
        Visit your {{ user.auth_provider | capitalize }} settings to change it.
      </p>
    </div>
  {% endif %}

  <div class="account-card">
    <h2 class="account-card-title">Signed in with</h2>
    <p class="account-field-readonly">{{ user.auth_provider | capitalize }}</p>
  </div>

  <div class="account-card">
    <h2 class="account-card-title">Other sessions</h2>
    <p class="account-section-description">
      Sign out of every other device where you're currently signed in.
    </p>
    <form method="POST" action="{{ url_for('main.account_sessions_revoke') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit" class="btn-danger">Sign out everywhere else</button>
    </form>
  </div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/web/test_account_routes.py -k password -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add nextreel/web/routes/account.py templates/account/security.html tests/web/test_account_routes.py
git commit -m "feat(account): password change revokes other sessions"
```

---

### Task 10: Sign-out-everywhere endpoint

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/web/test_account_routes.py`:

```python
async def test_sessions_revoke_calls_primitive(client, logged_in_user, mock_redis):
    mock_redis.scan.side_effect = [(0, [])]  # no other sessions
    response = await client.post(
        "/account/sessions/revoke",
        form={"csrf_token": logged_in_user["csrf"]},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert mock_redis.scan.await_count >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_account_routes.py -k sessions_revoke -v`
Expected: FAIL — route not found

- [ ] **Step 3: Implement the handler**

Add to `nextreel/web/routes/account.py`:

```python
@bp.route("/account/sessions/revoke", methods=["POST"])
@csrf_required
async def account_sessions_revoke():
    user_id = _require_user()
    redis_client = current_app.config.get("REDIS_CLIENT")
    if redis_client is None:
        abort(503, description="Session store unavailable.")
    current_sid = session.sid if hasattr(session, "sid") else session.get("_id")
    revoked = await revoke_user_sessions(redis_client, user_id, except_session_id=current_sid)
    logger.info("Account action: %s user=%s revoked=%d", "sessions_revoke", user_id, revoked)
    return redirect(url_for("main.account_view") + "?tab=security")
```

- [ ] **Step 4: Run test**

Run: `python3 -m pytest tests/web/test_account_routes.py -k sessions_revoke -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/account.py tests/web/test_account_routes.py
git commit -m "feat(account): standalone sign-out-everywhere endpoint"
```

---

## Phase 7: Preferences Tab

### Task 11: Save `exclude_watched_default` and `theme_preference`

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Modify: `templates/account/preferences.html`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_preferences_save_writes_exclude_and_theme(client, logged_in_user, db_pool):
    response = await client.post(
        "/account/preferences",
        form={
            "csrf_token": logged_in_user["csrf"],
            "exclude_watched_default": "on",
            "theme_preference": "dark",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    row = await db_pool.execute(
        "SELECT exclude_watched_default, theme_preference FROM users WHERE user_id = %s",
        [logged_in_user["user_id"]],
        fetch="one",
    )
    assert row["exclude_watched_default"] == 1
    assert row["theme_preference"] == "dark"


async def test_preferences_save_clears_theme_when_system(client, logged_in_user, db_pool):
    # 'system' means: store NULL (follow device)
    await client.post(
        "/account/preferences",
        form={
            "csrf_token": logged_in_user["csrf"],
            "exclude_watched_default": "on",
            "theme_preference": "system",
        },
    )
    row = await db_pool.execute(
        "SELECT theme_preference FROM users WHERE user_id = %s",
        [logged_in_user["user_id"]],
        fetch="one",
    )
    assert row["theme_preference"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_account_routes.py -k preferences_save -v`
Expected: FAIL

- [ ] **Step 3: Implement the handler**

```python
@bp.route("/account/preferences", methods=["POST"])
@csrf_required
async def account_preferences_save():
    user_id = _require_user()
    form = await request.form
    exclude = form.get("exclude_watched_default") == "on"
    theme_raw = form.get("theme_preference", "system")
    theme = theme_raw if theme_raw in ("light", "dark") else None

    db_pool = current_app.config["DB_POOL"]
    await user_preferences.set_exclude_watched_default(db_pool, user_id, exclude)
    await user_preferences.set_theme_preference(db_pool, user_id, theme)
    logger.info("Account action: %s user=%s", "preferences_save", user_id)
    return redirect(url_for("main.account_view") + "?tab=preferences")
```

- [ ] **Step 4: Create the template**

Replace `templates/account/preferences.html`:

```jinja
{% extends "account/layout.html" %}
{% block panel %}
  <div class="account-card">
    <h2 class="account-card-title">Viewing preferences</h2>
    <form method="POST" action="{{ url_for('main.account_preferences_save') }}" class="space-y-4">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

      <div class="account-field">
        <label class="auth-input-label" for="exclude_watched_default">
          <input id="exclude_watched_default" name="exclude_watched_default" type="checkbox"
                 {% if exclude_watched_default %}checked{% endif %}>
          Hide movies I've already watched
        </label>
      </div>

      <fieldset class="account-field">
        <legend class="auth-input-label">Theme</legend>
        {% for value, label in [('light','Light'), ('dark','Dark'), ('system','Use system setting')] %}
          <label style="display:block; margin:0.25rem 0;">
            <input type="radio" name="theme_preference" value="{{ value }}"
                   {% if (server_theme or 'system') == value %}checked{% endif %}> {{ label }}
          </label>
        {% endfor %}
      </fieldset>

      <button type="submit" class="auth-submit">Save preferences</button>
    </form>
  </div>

  <div class="account-card">
    <h2 class="account-card-title">Default filters</h2>
    {% if default_filters %}
      <p class="account-section-description">
        When you open Nextreel, these filters apply by default.
      </p>
      <ul style="margin:0 0 1rem 1.25rem; font-size:0.9375rem;">
        {% for k, v in default_filters.items() %}
          <li><strong>{{ k | replace('_',' ') | capitalize }}</strong>: {{ v }}</li>
        {% endfor %}
      </ul>
      <form method="POST" action="{{ url_for('main.account_filters_clear') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <button type="submit" class="btn-danger">Clear defaults</button>
      </form>
    {% else %}
      <p class="account-section-description">
        No default filters saved. Visit
        <a href="{{ url_for('main.set_filters') }}" class="auth-switch-link">Filters</a>
        and click <strong>Save as default</strong> to set them.
      </p>
    {% endif %}
  </div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/web/test_account_routes.py -k preferences_save -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add nextreel/web/routes/account.py templates/account/preferences.html tests/web/test_account_routes.py
git commit -m "feat(account): Preferences tab — exclude_watched + theme"
```

---

### Task 12: Save and clear default filter presets

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Modify: `templates/set_filters.html`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_filters_save_as_default_persists_payload(client, logged_in_user, db_pool):
    import json as _json
    # Payload mirrors what /filters submits — reuse existing filter form fields.
    response = await client.post(
        "/account/preferences/filters/save",
        form={
            "csrf_token": logged_in_user["csrf"],
            "genres": "Horror,Thriller",
            "min_year": "2000",
            "min_rating": "7.0",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    row = await db_pool.execute(
        "SELECT default_filters_json FROM users WHERE user_id = %s",
        [logged_in_user["user_id"]],
        fetch="one",
    )
    parsed = _json.loads(row["default_filters_json"])
    assert parsed  # non-empty
    assert "genres" in parsed or "min_year" in parsed


async def test_filters_clear_writes_null(client, logged_in_user, db_pool):
    await db_pool.execute(
        "UPDATE users SET default_filters_json = %s WHERE user_id = %s",
        ['{"genres": ["X"]}', logged_in_user["user_id"]],
        fetch="none",
    )
    response = await client.post(
        "/account/preferences/filters/clear",
        form={"csrf_token": logged_in_user["csrf"]},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    row = await db_pool.execute(
        "SELECT default_filters_json FROM users WHERE user_id = %s",
        [logged_in_user["user_id"]],
        fetch="one",
    )
    assert row["default_filters_json"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_account_routes.py -k "filters_save or filters_clear" -v`
Expected: FAIL

- [ ] **Step 3: Implement handlers**

Add to `nextreel/web/routes/account.py`:

```python
from movies.filter_parser import parse_filter_form  # use existing parser


@bp.route("/account/preferences/filters/save", methods=["POST"])
@csrf_required
async def account_filters_save():
    user_id = _require_user()
    form = await request.form
    # Reuse the existing filter parser; it returns a dict that round-trips
    # through the filter application code. If parse_filter_form has a
    # different name in the current codebase, adjust the import above.
    filters = parse_filter_form(form)
    db_pool = current_app.config["DB_POOL"]
    await user_preferences.set_default_filters(db_pool, user_id, filters)
    logger.info("Account action: %s user=%s", "filters_save_default", user_id)
    return redirect(url_for("main.account_view") + "?tab=preferences")


@bp.route("/account/preferences/filters/clear", methods=["POST"])
@csrf_required
async def account_filters_clear():
    user_id = _require_user()
    db_pool = current_app.config["DB_POOL"]
    await user_preferences.clear_default_filters(db_pool, user_id)
    logger.info("Account action: %s user=%s", "filters_clear_default", user_id)
    return redirect(url_for("main.account_view") + "?tab=preferences")
```

**Note:** `movies/filter_parser.py` exports a function used by the existing `/filters` route. Open it and pick whichever function returns the normalized dict — e.g. `parse_filter_form(form) -> dict`. If the function takes different arguments, adapt the call but do not duplicate parsing logic.

- [ ] **Step 4: Add "Save as default" button to the filter page**

Open `templates/set_filters.html`. Find the Apply button block and add a sibling form that POSTs to `/account/preferences/filters/save` carrying the same form fields. One concrete approach: include a second submit button inside the existing form whose `formaction` attribute overrides the destination:

```jinja
<button type="submit" class="auth-submit">Apply</button>
{% if current_user_id %}
<button type="submit"
        formaction="{{ url_for('main.account_filters_save') }}"
        class="auth-submit" style="background:var(--color-muted);">
  Save as default
</button>
{% endif %}
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/web/test_account_routes.py -k "filters_save or filters_clear" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add nextreel/web/routes/account.py templates/set_filters.html tests/web/test_account_routes.py
git commit -m "feat(account): save & clear default filter presets"
```

---

## Phase 8: Data Tab — Import

### Task 13: Letterboxd CSV upload endpoint + progress page

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Create: `templates/account/import_progress.html`
- Modify: `templates/account/data.html`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_letterboxd_upload_creates_import_row_and_enqueues(
    client, logged_in_user, db_pool, mock_arq, mock_redis
):
    from io import BytesIO
    csv_body = b"Date,Name,Year\n2024-01-01,The Matrix,1999\n"
    response = await client.post(
        "/account/import/letterboxd",
        data={
            "csv": (BytesIO(csv_body), "watched.csv", "text/csv"),
            "csrf_token": logged_in_user["csrf"],
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/account/import/" in response.headers["Location"]

    row = await db_pool.execute(
        "SELECT status, user_id FROM letterboxd_imports WHERE user_id = %s",
        [logged_in_user["user_id"]],
        fetch="one",
    )
    assert row is not None
    assert row["status"] == "pending"
    assert mock_arq.enqueue_job.await_count == 1
    args, _ = mock_arq.enqueue_job.await_args
    assert args[0] == "import_letterboxd"


async def test_letterboxd_upload_rejects_oversize(client, logged_in_user):
    from io import BytesIO
    big = b"x" * (6 * 1024 * 1024)
    response = await client.post(
        "/account/import/letterboxd",
        data={
            "csv": (BytesIO(big), "watched.csv", "text/csv"),
            "csrf_token": logged_in_user["csrf"],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 413 or response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_account_routes.py -k letterboxd_upload -v`
Expected: FAIL

- [ ] **Step 3: Implement upload handler**

Add to `nextreel/web/routes/account.py`:

```python
from uuid import uuid4

MAX_LETTERBOXD_CSV_BYTES = 5 * 1024 * 1024  # 5 MB


@bp.route("/account/import/letterboxd", methods=["POST"])
@csrf_required
@rate_limited("account_letterboxd_import")
async def account_letterboxd_upload():
    user_id = _require_user()
    files = await request.files
    upload = files.get("csv")
    if upload is None:
        abort(400, description="No file provided.")

    data = upload.read()
    if len(data) == 0:
        abort(400, description="File is empty.")
    if len(data) > MAX_LETTERBOXD_CSV_BYTES:
        abort(413, description="File too large (max 5 MB).")

    import_id = uuid4().hex
    now = utcnow()
    db_pool = current_app.config["DB_POOL"]
    await db_pool.execute(
        """
        INSERT INTO letterboxd_imports
          (import_id, user_id, status, processed, matched, skipped, failed,
           created_at, updated_at)
        VALUES (%s, %s, 'pending', 0, 0, 0, 0, %s, %s)
        """,
        [import_id, user_id, now, now],
        fetch="none",
    )

    redis_client = current_app.config.get("REDIS_CLIENT")
    if redis_client is None:
        abort(503, description="Storage unavailable.")
    await redis_client.set(
        f"letterboxd:import:{import_id}:csv",
        data,
        ex=60 * 60 * 24,
    )

    arq_pool = current_app.config.get("ARQ_POOL")
    if arq_pool is None:
        abort(503, description="Job queue unavailable.")
    await arq_pool.enqueue_job("import_letterboxd", import_id)

    logger.info("Account action: %s user=%s import_id=%s", "letterboxd_upload",
                user_id, import_id)
    return redirect(url_for("main.account_import_progress", import_id=import_id))


@bp.route("/account/import/<import_id>")
async def account_import_progress(import_id: str):
    user_id = _require_user()
    db_pool = current_app.config["DB_POOL"]
    row = await db_pool.execute(
        """
        SELECT import_id, status, total_rows, processed, matched, skipped,
               failed, error_message, created_at, completed_at
        FROM letterboxd_imports
        WHERE import_id = %s AND user_id = %s
        """,
        [import_id, user_id],
        fetch="one",
    )
    if not row:
        abort(404)
    return await render_template(
        "account/import_progress.html",
        active_tab="data",
        import_row=row,
        user=await get_user_by_id(db_pool, user_id),
    )


@bp.route("/account/import/<import_id>/status")
async def account_import_status(import_id: str):
    from quart import jsonify
    user_id = _require_user()
    db_pool = current_app.config["DB_POOL"]
    row = await db_pool.execute(
        """
        SELECT status, total_rows, processed, matched, skipped, failed, error_message
        FROM letterboxd_imports
        WHERE import_id = %s AND user_id = %s
        """,
        [import_id, user_id],
        fetch="one",
    )
    if not row:
        abort(404)
    return jsonify(dict(row))
```

- [ ] **Step 4: Create `templates/account/import_progress.html`**

```jinja
{% extends "account/layout.html" %}
{% block panel %}
  <div class="account-card">
    <h2 class="account-card-title">Importing from Letterboxd…</h2>
    <p class="account-section-description" data-import-id="{{ import_row.import_id }}">
      <span id="import-status">{{ import_row.status }}</span> —
      <span id="import-processed">{{ import_row.processed }}</span> of
      <span id="import-total">{{ import_row.total_rows or '?' }}</span> rows
    </p>
    <ul style="font-size:0.9375rem;">
      <li>Matched: <span id="import-matched">{{ import_row.matched }}</span></li>
      <li>Skipped: <span id="import-skipped">{{ import_row.skipped }}</span></li>
      <li>Failed: <span id="import-failed">{{ import_row.failed }}</span></li>
    </ul>
    <p><a href="{{ url_for('main.account_view') }}?tab=data" class="auth-switch-link">← Back to Data tab</a></p>
  </div>

  <script>
    (function () {
      const id = document.querySelector('[data-import-id]').dataset.importId;
      let polling = true;
      async function tick() {
        if (!polling) return;
        try {
          const r = await fetch('/account/import/' + id + '/status');
          if (!r.ok) { polling = false; return; }
          const d = await r.json();
          document.getElementById('import-status').textContent = d.status;
          document.getElementById('import-processed').textContent = d.processed;
          document.getElementById('import-total').textContent = d.total_rows ?? '?';
          document.getElementById('import-matched').textContent = d.matched;
          document.getElementById('import-skipped').textContent = d.skipped;
          document.getElementById('import-failed').textContent = d.failed;
          if (d.status === 'completed' || d.status === 'failed') { polling = false; return; }
        } catch (e) {}
        setTimeout(tick, 2000);
      }
      tick();
    })();
  </script>
{% endblock %}
```

- [ ] **Step 5: Replace `templates/account/data.html`**

```jinja
{% extends "account/layout.html" %}
{% block panel %}
  <div class="account-card">
    <h2 class="account-card-title">Import from Letterboxd</h2>
    <p class="account-section-description">
      Upload your Letterboxd watched CSV (max 5 MB). We'll match titles to IMDb and add them to your watched list.
    </p>
    <form method="POST" action="{{ url_for('main.account_letterboxd_upload') }}"
          enctype="multipart/form-data" class="space-y-3">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <input type="file" name="csv" accept=".csv" required>
      <button type="submit" class="auth-submit">Upload</button>
    </form>
  </div>

  <div class="account-card">
    <h2 class="account-card-title">Export your watched list</h2>
    <p class="account-section-description">Download a copy for backup or to move to another tracker.</p>
    <a href="{{ url_for('main.account_export_watched_csv') }}" class="auth-submit" style="display:inline-block; margin-right:0.5rem;">Download CSV</a>
    <a href="{{ url_for('main.account_export_watched_json') }}" class="auth-submit" style="display:inline-block;">Download JSON</a>
  </div>

  <div class="account-card">
    <h2 class="account-card-title">Clear watched history</h2>
    <p class="account-section-description">Remove every movie from your watched list. This cannot be undone.</p>
    <form method="POST" action="{{ url_for('main.account_watched_clear') }}"
          onsubmit="return confirm('Clear all watched movies? This cannot be undone.');">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit" class="btn-danger">Clear all watched history</button>
    </form>
  </div>
{% endblock %}
```

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/web/test_account_routes.py -k letterboxd_upload -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add nextreel/web/routes/account.py templates/account/data.html templates/account/import_progress.html tests/web/test_account_routes.py
git commit -m "feat(account): Letterboxd CSV upload + progress page"
```

---

### Task 14: Register `import_letterboxd` arq job

**Files:**
- Modify: `nextreel/workers/worker.py`
- Test: `tests/workers/test_letterboxd_import_job.py`

- [ ] **Step 1: Write failing tests**

Create `tests/workers/test_letterboxd_import_job.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def ctx():
    return {
        "db_pool": AsyncMock(),
        "redis": AsyncMock(),
        "tmdb_client": MagicMock(),
    }


async def test_job_sets_status_running_then_completed(ctx, monkeypatch):
    from nextreel.workers import worker as worker_module
    import_id = "abc123"

    csv_body = b"Date,Name,Year\n2024-01-01,The Matrix,1999\n"
    ctx["redis"].get.return_value = csv_body
    ctx["db_pool"].execute.side_effect = [
        {"user_id": "u1", "import_id": import_id},  # initial SELECT
        None,  # UPDATE running
        None,  # flush
        None,  # final UPDATE completed
    ]

    async def fake_run(*args, **kwargs):
        return {"matched": 1, "skipped": 0, "failed": 0, "total": 1}

    monkeypatch.setattr(worker_module, "_run_letterboxd_import",
                        AsyncMock(side_effect=fake_run))

    await worker_module.import_letterboxd(ctx, import_id)

    executed_sql = [c.args[0] for c in ctx["db_pool"].execute.await_args_list]
    assert any("status = 'running'" in s or "'running'" in s for s in executed_sql)
    assert any("'completed'" in s for s in executed_sql)


async def test_job_sets_failed_on_exception(ctx, monkeypatch):
    from nextreel.workers import worker as worker_module
    import_id = "abc123"

    ctx["redis"].get.return_value = b"bad"
    ctx["db_pool"].execute.side_effect = [
        {"user_id": "u1", "import_id": import_id},
        None,
        None,  # failing UPDATE
    ]

    async def boom(*args, **kwargs):
        raise RuntimeError("parse failure")

    monkeypatch.setattr(worker_module, "_run_letterboxd_import", AsyncMock(side_effect=boom))

    with pytest.raises(RuntimeError):
        await worker_module.import_letterboxd(ctx, import_id)

    executed_sql = [c.args[0] for c in ctx["db_pool"].execute.await_args_list]
    assert any("'failed'" in s for s in executed_sql)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/workers/test_letterboxd_import_job.py -v`
Expected: FAIL — function not defined

- [ ] **Step 3: Implement the job**

Add to `nextreel/workers/worker.py` (or the underlying implementation module that `worker.py` re-exports — follow the existing conventions):

```python
import time
from uuid import uuid4

from infra.time_utils import utcnow
from logging_config import get_logger
from movies.letterboxd_import import parse_letterboxd_csv, match_title_to_tconst

logger = get_logger(__name__)


async def _run_letterboxd_import(ctx, import_id, user_id, csv_body):
    """Parse CSV, resolve rows to tconsts, insert into user_watched_movies.

    Returns dict with matched/skipped/failed/total counts. Flushes progress
    back to `letterboxd_imports` every 25 rows or 2 seconds.
    """
    db_pool = ctx["db_pool"]
    rows = list(parse_letterboxd_csv(csv_body))
    total = len(rows)

    await db_pool.execute(
        "UPDATE letterboxd_imports SET total_rows = %s, updated_at = %s WHERE import_id = %s",
        [total, utcnow(), import_id],
        fetch="none",
    )

    matched = skipped = failed = 0
    last_flush = time.monotonic()

    for i, row in enumerate(rows, start=1):
        try:
            tconst = await match_title_to_tconst(db_pool, row.title, row.year)
            if tconst is None:
                skipped += 1
            else:
                await db_pool.execute(
                    """
                    INSERT INTO user_watched_movies (user_id, tconst, watched_at)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE watched_at = watched_at
                    """,
                    [user_id, tconst, row.watched_at or utcnow()],
                    fetch="none",
                )
                matched += 1
        except Exception:  # noqa: BLE001
            logger.exception("Row import failed: %r", row)
            failed += 1

        if i % 25 == 0 or (time.monotonic() - last_flush) > 2.0:
            await db_pool.execute(
                """
                UPDATE letterboxd_imports
                SET processed = %s, matched = %s, skipped = %s, failed = %s,
                    updated_at = %s
                WHERE import_id = %s
                """,
                [i, matched, skipped, failed, utcnow(), import_id],
                fetch="none",
            )
            last_flush = time.monotonic()

    return {"matched": matched, "skipped": skipped, "failed": failed, "total": total}


async def import_letterboxd(ctx, import_id: str) -> None:
    db_pool = ctx["db_pool"]
    redis_client = ctx["redis"]

    row = await db_pool.execute(
        "SELECT user_id FROM letterboxd_imports WHERE import_id = %s",
        [import_id],
        fetch="one",
    )
    if not row:
        logger.warning("Letterboxd import row missing: %s", import_id)
        return
    user_id = row["user_id"]

    await db_pool.execute(
        "UPDATE letterboxd_imports SET status = 'running', updated_at = %s WHERE import_id = %s",
        [utcnow(), import_id],
        fetch="none",
    )

    csv_body = await redis_client.get(f"letterboxd:import:{import_id}:csv")
    if csv_body is None:
        await db_pool.execute(
            """
            UPDATE letterboxd_imports
            SET status = 'failed', error_message = 'CSV blob missing', updated_at = %s
            WHERE import_id = %s
            """,
            [utcnow(), import_id],
            fetch="none",
        )
        return

    try:
        counts = await _run_letterboxd_import(ctx, import_id, user_id, csv_body)
    except Exception as exc:
        await db_pool.execute(
            """
            UPDATE letterboxd_imports
            SET status = 'failed', error_message = %s, updated_at = %s
            WHERE import_id = %s
            """,
            [str(exc)[:500], utcnow(), import_id],
            fetch="none",
        )
        raise

    await db_pool.execute(
        """
        UPDATE letterboxd_imports
        SET status = 'completed',
            processed = %s, matched = %s, skipped = %s, failed = %s,
            total_rows = %s, updated_at = %s, completed_at = %s
        WHERE import_id = %s
        """,
        [
            counts["total"], counts["matched"], counts["skipped"], counts["failed"],
            counts["total"], utcnow(), utcnow(), import_id,
        ],
        fetch="none",
    )
    await redis_client.delete(f"letterboxd:import:{import_id}:csv")
```

Register it in `WorkerSettings.functions` (same pattern as existing jobs).

**Note:** `movies/letterboxd_import.py` already exists. Verify that `parse_letterboxd_csv` and `match_title_to_tconst` exist there; if they have different names (e.g. `iter_letterboxd_rows`, `resolve_title`), adjust the imports. Do not reimplement parsing/matching — reuse what's there.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/workers/test_letterboxd_import_job.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nextreel/workers/worker.py tests/workers/test_letterboxd_import_job.py
git commit -m "feat(workers): add import_letterboxd arq job"
```

---

## Phase 9: Data Tab — Export & Clear

### Task 15: Streaming CSV and JSON exports

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_export_csv_returns_attachment(client, logged_in_user, db_pool):
    await db_pool.execute(
        "INSERT INTO user_watched_movies (user_id, tconst, watched_at) VALUES (%s, %s, %s)",
        [logged_in_user["user_id"], "tt0133093", "2024-01-01 00:00:00"],
        fetch="none",
    )
    response = await client.get("/account/export/watched.csv")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in response.headers["Content-Disposition"]
    body = (await response.get_data()).decode()
    assert "tt0133093" in body or "Matrix" in body
    assert body.splitlines()[0].startswith("Date,Name,Year")


async def test_export_json_is_valid_and_scoped_to_user(client, logged_in_user, db_pool):
    import json as _json
    await db_pool.execute(
        "INSERT INTO user_watched_movies (user_id, tconst, watched_at) VALUES (%s, %s, %s)",
        [logged_in_user["user_id"], "tt0133093", "2024-01-01 00:00:00"],
        fetch="none",
    )
    await db_pool.execute(
        "INSERT INTO user_watched_movies (user_id, tconst, watched_at) VALUES (%s, %s, %s)",
        ["other-user", "tt0111161", "2024-01-01 00:00:00"],
        fetch="none",
    )
    response = await client.get("/account/export/watched.json")
    assert response.status_code == 200
    body = (await response.get_data()).decode()
    parsed = _json.loads(body)
    tconsts = {row["tconst"] for row in parsed}
    assert "tt0133093" in tconsts
    assert "tt0111161" not in tconsts  # scoped to the logged-in user
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_account_routes.py -k export -v`
Expected: FAIL

- [ ] **Step 3: Implement handlers**

Add to `nextreel/web/routes/account.py`:

```python
import csv
import io
from quart import Response


@bp.route("/account/export/watched.csv")
@rate_limited("account_export")
async def account_export_watched_csv():
    user_id = _require_user()
    db_pool = current_app.config["DB_POOL"]
    rows = await db_pool.execute(
        """
        SELECT w.tconst, w.watched_at,
               COALESCE(p.title, '') AS title,
               COALESCE(p.year, '')  AS year
        FROM user_watched_movies w
        LEFT JOIN movie_projection p ON p.tconst = w.tconst
        WHERE w.user_id = %s
        ORDER BY w.watched_at DESC
        """,
        [user_id],
        fetch="all",
    ) or []

    async def stream():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Date", "Name", "Year", "Letterboxd URI"])
        yield buf.getvalue()
        for r in rows:
            buf.seek(0); buf.truncate(0)
            date = r["watched_at"].strftime("%Y-%m-%d") if r.get("watched_at") else ""
            writer.writerow([date, r["title"], r["year"], ""])
            yield buf.getvalue()

    filename = f"nextreel-watched-{utcnow().strftime('%Y-%m-%d')}.csv"
    return Response(
        stream(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/account/export/watched.json")
@rate_limited("account_export")
async def account_export_watched_json():
    import json as _json
    user_id = _require_user()
    db_pool = current_app.config["DB_POOL"]
    rows = await db_pool.execute(
        """
        SELECT w.tconst, w.watched_at,
               p.title, p.year, p.poster_url
        FROM user_watched_movies w
        LEFT JOIN movie_projection p ON p.tconst = w.tconst
        WHERE w.user_id = %s
        ORDER BY w.watched_at DESC
        """,
        [user_id],
        fetch="all",
    ) or []

    async def stream():
        yield "["
        first = True
        for r in rows:
            if not first:
                yield ","
            first = False
            yield _json.dumps({
                "tconst": r["tconst"],
                "title": r.get("title"),
                "year": r.get("year"),
                "watched_at": r["watched_at"].isoformat() if r.get("watched_at") else None,
                "poster_url": r.get("poster_url"),
            })
        yield "]"

    filename = f"nextreel-watched-{utcnow().strftime('%Y-%m-%d')}.json"
    return Response(
        stream(),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

**Note:** if `movie_projection` column names differ (e.g. `primary_title` instead of `title`), adjust the SELECT.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/web/test_account_routes.py -k export -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/account.py tests/web/test_account_routes.py
git commit -m "feat(account): streaming CSV and JSON watched-list exports"
```

---

### Task 16: Clear watched history

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing test**

```python
async def test_watched_clear_deletes_only_current_user_rows(client, logged_in_user, db_pool):
    await db_pool.execute(
        "INSERT INTO user_watched_movies (user_id, tconst, watched_at) VALUES (%s, %s, %s)",
        [logged_in_user["user_id"], "tt1", "2024-01-01 00:00:00"],
        fetch="none",
    )
    await db_pool.execute(
        "INSERT INTO user_watched_movies (user_id, tconst, watched_at) VALUES (%s, %s, %s)",
        ["other-user", "tt2", "2024-01-01 00:00:00"],
        fetch="none",
    )
    response = await client.post(
        "/account/watched/clear",
        form={"csrf_token": logged_in_user["csrf"]},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    mine = await db_pool.execute(
        "SELECT COUNT(*) AS c FROM user_watched_movies WHERE user_id = %s",
        [logged_in_user["user_id"]], fetch="one",
    )
    others = await db_pool.execute(
        "SELECT COUNT(*) AS c FROM user_watched_movies WHERE user_id = 'other-user'",
        fetch="one",
    )
    assert mine["c"] == 0
    assert others["c"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_account_routes.py -k watched_clear -v`
Expected: FAIL

- [ ] **Step 3: Implement handler**

```python
@bp.route("/account/watched/clear", methods=["POST"])
@csrf_required
async def account_watched_clear():
    user_id = _require_user()
    db_pool = current_app.config["DB_POOL"]
    await db_pool.execute(
        "DELETE FROM user_watched_movies WHERE user_id = %s",
        [user_id],
        fetch="none",
    )
    logger.info("Account action: %s user=%s", "watched_clear", user_id)
    return redirect(url_for("main.account_view") + "?tab=data")
```

- [ ] **Step 4: Run test**

Run: `python3 -m pytest tests/web/test_account_routes.py -k watched_clear -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/account.py tests/web/test_account_routes.py
git commit -m "feat(account): clear watched history endpoint"
```

---

## Phase 10: Danger Zone

### Task 17: Delete account with type-to-confirm

**Files:**
- Modify: `nextreel/web/routes/account.py`
- Modify: `templates/account/danger.html`
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_delete_account_removes_user_and_related_rows(
    client, logged_in_user, db_pool, mock_redis
):
    user_id = logged_in_user["user_id"]
    await db_pool.execute(
        "INSERT INTO user_watched_movies (user_id, tconst, watched_at) VALUES (%s,%s,%s)",
        [user_id, "tt1", "2024-01-01 00:00:00"], fetch="none",
    )
    response = await client.post(
        "/account/delete",
        form={
            "csrf_token": logged_in_user["csrf"],
            "confirm_email": logged_in_user["email"],
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["Location"].rstrip("/") in ("", "/")

    user_row = await db_pool.execute(
        "SELECT 1 FROM users WHERE user_id = %s", [user_id], fetch="one"
    )
    watched_row = await db_pool.execute(
        "SELECT 1 FROM user_watched_movies WHERE user_id = %s", [user_id], fetch="one"
    )
    assert user_row is None
    assert watched_row is None


async def test_delete_account_requires_matching_email(client, logged_in_user, db_pool):
    response = await client.post(
        "/account/delete",
        form={
            "csrf_token": logged_in_user["csrf"],
            "confirm_email": "wrong@example.com",
        },
    )
    assert response.status_code == 400
    row = await db_pool.execute(
        "SELECT 1 FROM users WHERE user_id = %s",
        [logged_in_user["user_id"]], fetch="one",
    )
    assert row is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_account_routes.py -k delete_account -v`
Expected: FAIL

- [ ] **Step 3: Implement handler**

```python
@bp.route("/account/delete", methods=["POST"])
@csrf_required
@rate_limited("account_delete")
async def account_delete():
    user_id = _require_user()
    form = await request.form
    typed = (form.get("confirm_email") or "").strip().lower()

    db_pool = current_app.config["DB_POOL"]
    user = await get_user_by_id(db_pool, user_id)
    if not user:
        abort(400)
    if typed != user["email"].strip().lower():
        abort(400, description="Typed email does not match your account.")

    # Ordered cascade — wrap in a single transaction if the pool exposes one.
    await db_pool.execute(
        "DELETE FROM user_watched_movies   WHERE user_id = %s", [user_id], fetch="none"
    )
    await db_pool.execute(
        "DELETE FROM user_navigation_state WHERE user_id = %s", [user_id], fetch="none"
    )
    await db_pool.execute(
        "DELETE FROM letterboxd_imports    WHERE user_id = %s", [user_id], fetch="none"
    )
    await db_pool.execute(
        "DELETE FROM users                 WHERE user_id = %s", [user_id], fetch="none"
    )

    redis_client = current_app.config.get("REDIS_CLIENT")
    if redis_client is not None:
        await revoke_user_sessions(redis_client, user_id, except_session_id=None)

    session.clear()
    logger.info("Account action: %s user=%s", "account_delete", user_id)
    return redirect(url_for("main.home"))
```

- [ ] **Step 4: Replace `templates/account/danger.html`**

```jinja
{% extends "account/layout.html" %}
{% block panel %}
  <div class="account-card account-danger-card">
    <h2 class="account-card-title">Delete my account</h2>
    <p class="account-section-description">
      This removes your account and all associated data — your watched list, your preferences,
      and any in-progress imports. This cannot be undone.
    </p>
    <button type="button" class="btn-danger"
            onclick="document.getElementById('delete-modal').classList.add('open');">
      Delete my account permanently
    </button>
  </div>

  <div id="delete-modal" class="modal-backdrop" role="dialog" aria-modal="true">
    <div class="modal-panel">
      <h3>Confirm account deletion</h3>
      <p class="account-section-description">
        To confirm, type your email address below: <strong>{{ user.email }}</strong>
      </p>
      <form method="POST" action="{{ url_for('main.account_delete') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <input id="confirm_email_input" name="confirm_email" type="email" required
               autocomplete="off" class="auth-input" placeholder="you@example.com">
        <div class="modal-actions">
          <button type="button" class="auth-submit" style="background:var(--color-muted);"
                  onclick="document.getElementById('delete-modal').classList.remove('open');">Cancel</button>
          <button id="delete-confirm-btn" type="submit" class="btn-danger" disabled>Delete permanently</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    (function () {
      const target = "{{ user.email | lower }}";
      const input = document.getElementById('confirm_email_input');
      const btn   = document.getElementById('delete-confirm-btn');
      input.addEventListener('input', function () {
        btn.disabled = input.value.trim().toLowerCase() !== target;
      });
    })();
  </script>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/web/test_account_routes.py -k delete_account -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add nextreel/web/routes/account.py templates/account/danger.html tests/web/test_account_routes.py
git commit -m "feat(account): account deletion with type-to-confirm cascade"
```

---

## Phase 11: Visual Polish & Navbar Integration

### Task 18: Server-rendered theme on all page shells

**Files:**
- Modify: `templates/login.html`, `templates/home.html`, `templates/movie.html`, `templates/watched_list.html`, `templates/set_filters.html`, `templates/register.html`

- [ ] **Step 1: Update each page shell**

For each listed template, locate the existing pre-paint theme script:

```javascript
(() => {
  try {
    const pref = localStorage.getItem('nr-theme');
    if (pref === 'light' || pref === 'dark') {
      document.documentElement.setAttribute('data-theme', pref);
    }
  } catch (e) {}
})();
```

Replace it with:

```javascript
(() => {
  try {
    const pref = localStorage.getItem('nr-theme');
    if (pref === 'light' || pref === 'dark') {
      document.documentElement.setAttribute('data-theme', pref);
      return;
    }
    const server = document.documentElement.getAttribute('data-theme-server');
    if (server === 'light' || server === 'dark') {
      document.documentElement.setAttribute('data-theme', server);
    }
  } catch (e) {}
})();
```

And on each template's `<html>` tag, add `{% if server_theme %}data-theme-server="{{ server_theme }}"{% endif %}`.

Then update the shared context-processor or `before_request` handler (see `nextreel/web/routes/shared.py` `inject_csrf_token` for the pattern) to pass `server_theme` into every authenticated template. Register a new `@bp.app_context_processor`:

```python
@bp.app_context_processor
async def inject_server_theme():
    user_id = _current_user_id()
    if not user_id:
        return {"server_theme": None}
    db_pool = current_app.config.get("DB_POOL")
    if db_pool is None:
        return {"server_theme": None}
    try:
        theme = await user_preferences.get_theme_preference(db_pool, user_id)
    except Exception:
        theme = None
    return {"server_theme": theme}
```

Place this in `nextreel/web/routes/shared.py` so every template inherits it.

- [ ] **Step 2: Manual smoke test**

1. Run: `python3 app.py`
2. Sign in, go to `/account?tab=preferences`, set theme to Dark, save.
3. Clear `localStorage.nr-theme` in devtools.
4. Hard-refresh any page — dark theme should apply without flash.

- [ ] **Step 3: Commit**

```bash
git add templates/ nextreel/web/routes/shared.py
git commit -m "feat(theme): server-rendered theme preference as localStorage fallback"
```

---

### Task 19: Navbar avatar dropdown

**Files:**
- Modify: `templates/navbar_modern.html`

- [ ] **Step 1: Update the desktop nav for logged-in users**

In `templates/navbar_modern.html`, replace the desktop logged-in branch:

```jinja
{% if current_user_id %}
  <div class="account-avatar-dropdown">
    <button type="button" class="account-avatar-dropdown-trigger" id="avatarBtn"
            aria-haspopup="true" aria-expanded="false" aria-controls="avatarMenu">
      {{ user_avatar(current_user, 'sm') }}
    </button>
    <div id="avatarMenu" class="account-avatar-dropdown-menu" role="menu">
      <a href="{{ url_for('main.account_view') }}?tab=profile" role="menuitem">Account</a>
      <a href="{{ url_for('main.watched_list_page') }}" role="menuitem">Watched</a>
      <form method="POST" action="/logout" style="display:contents;">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <button type="submit" role="menuitem">Log out</button>
      </form>
    </div>
  </div>
{% else %}
  <a href="/login" class="navbar-link">Log In</a>
{% endif %}
```

And wire the toggle in the existing `<script>` block at the bottom of the file:

```javascript
var avatarBtn = document.getElementById('avatarBtn');
var avatarMenu = document.getElementById('avatarMenu');
if (avatarBtn && avatarMenu) {
  avatarBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    var isOpen = avatarMenu.classList.toggle('open');
    avatarBtn.setAttribute('aria-expanded', String(isOpen));
  });
  document.addEventListener('click', function () {
    avatarMenu.classList.remove('open');
    avatarBtn.setAttribute('aria-expanded', 'false');
  });
}
```

Also add an **Account** link to the mobile panel, before the existing **Watched** link:

```jinja
{% if current_user_id %}
<a href="{{ url_for('main.account_view') }}?tab=profile">Account</a>
{% endif %}
```

- [ ] **Step 2: Ensure `current_user` is available in templates**

The existing `_current_user_id()` returns an ID. For the avatar macro, templates need the full user row. Extend the shared context processor (next to `inject_csrf_token`):

```python
@bp.app_context_processor
async def inject_current_user():
    user_id = _current_user_id()
    if not user_id:
        return {"current_user": None}
    db_pool = current_app.config.get("DB_POOL")
    if db_pool is None:
        return {"current_user": None}
    try:
        user = await get_user_by_id(db_pool, user_id)
    except Exception:
        user = None
    return {"current_user": user}
```

- [ ] **Step 3: Manual smoke test**

Run the app, sign in, and confirm: avatar appears in navbar, clicking it opens the dropdown, clicking outside closes it, menu items navigate correctly.

- [ ] **Step 4: Commit**

```bash
git add templates/navbar_modern.html nextreel/web/routes/shared.py
git commit -m "feat(navbar): avatar dropdown for logged-in users"
```

---

## Phase 12: Final Integration

### Task 20: Apply default filters on filter page first load

**Files:**
- Modify: `nextreel/web/routes/navigation.py`
- Test: `tests/web/test_routes_navigation.py`

- [ ] **Step 1: Write failing test**

Append to `tests/web/test_routes_navigation.py`:

```python
async def test_filters_page_seeds_from_user_defaults(client, logged_in_user, db_pool):
    import json as _json
    await db_pool.execute(
        "UPDATE users SET default_filters_json = %s WHERE user_id = %s",
        [_json.dumps({"genres": ["Horror"], "min_year": 2000}), logged_in_user["user_id"]],
        fetch="none",
    )
    response = await client.get("/filters")
    assert response.status_code == 200
    body = (await response.get_data()).decode()
    # Existing template echoes selected genres and min_year — check for Horror.
    assert "Horror" in body
    assert "2000" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_routes_navigation.py -k defaults -v`
Expected: FAIL

- [ ] **Step 3: Seed the filter form from defaults on first load**

In the existing `set_filters` GET handler in `nextreel/web/routes/navigation.py`, load the user's defaults when the session has no active filters yet:

```python
from session import user_preferences


@bp.route("/filters")
async def set_filters():
    user_id = _current_user_id()
    state = _current_state()
    active_filters = getattr(state, "filters", None)

    if not active_filters and user_id:
        db_pool = current_app.config["DB_POOL"]
        defaults = await user_preferences.get_default_filters(db_pool, user_id)
        if defaults:
            active_filters = defaults

    return await render_template(
        "set_filters.html",
        filters=active_filters or {},
        # ... rest of context unchanged
    )
```

The existing handler likely already renders `filters`; adapt the variable name to the current code. The goal is: when the logged-in user has defaults and no session-level filter yet, those defaults prefill the form.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/web/test_routes_navigation.py -k defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/navigation.py tests/web/test_routes_navigation.py
git commit -m "feat(filters): seed /filters form from user default_filters_json"
```

---

### Task 21: End-to-end smoke test

**Files:**
- Test: `tests/web/test_account_routes.py`

- [ ] **Step 1: Write the smoke test**

Append to `tests/web/test_account_routes.py`:

```python
async def test_all_tabs_render_for_logged_in_user(client, logged_in_user):
    for tab in ("profile", "security", "preferences", "data", "danger"):
        response = await client.get(f"/account?tab={tab}")
        assert response.status_code == 200, f"tab={tab} failed"
        body = (await response.get_data()).decode()
        assert "account-panel" in body


async def test_all_account_posts_require_csrf(client, logged_in_user):
    endpoints = [
        ("/account/profile",                     {"display_name": "x"}),
        ("/account/password",                    {"current_password": "a"}),
        ("/account/sessions/revoke",             {}),
        ("/account/preferences",                 {}),
        ("/account/preferences/filters/clear",   {}),
        ("/account/watched/clear",               {}),
        ("/account/delete",                      {"confirm_email": "x"}),
    ]
    for path, form in endpoints:
        response = await client.post(path, form=form)
        assert response.status_code in (400, 403), f"{path} accepted missing CSRF"


async def test_all_account_posts_require_login(client):
    endpoints = [
        "/account/profile",
        "/account/password",
        "/account/sessions/revoke",
        "/account/preferences",
        "/account/preferences/filters/save",
        "/account/preferences/filters/clear",
        "/account/watched/clear",
        "/account/delete",
        "/account/import/letterboxd",
    ]
    for path in endpoints:
        response = await client.post(path, form={}, follow_redirects=False)
        assert response.status_code in (302, 303, 401), f"{path} allowed anonymous"
```

- [ ] **Step 2: Run the full account test suite**

Run: `python3 -m pytest tests/web/test_account_routes.py -v`
Expected: ALL PASS

- [ ] **Step 3: Run the full project test suite**

Run: `python3 -m pytest tests/ -v`
Expected: ALL PASS (no regressions)

- [ ] **Step 4: Lint and format**

```bash
black . --line-length 100
flake8 . --exclude=venv,node_modules
mypy . --ignore-missing-imports
```

Fix any issues raised.

- [ ] **Step 5: Manual visual review**

1. Run `python3 app.py` and open in browser.
2. Walk through every tab side-by-side with `/movie`, `/watched`, and `/login`.
3. Confirm: heading weights match, card borders match, button heights/spacing match, both light and dark themes render correctly, navbar avatar sits at the same height as the existing nav links.

- [ ] **Step 6: Commit**

```bash
git add tests/web/test_account_routes.py
git commit -m "test(account): cross-tab smoke + auth/csrf coverage"
```

---

## Self-Review

**Spec coverage check — every section/requirement maps to a task:**

- §2.1 Routing → Tasks 7, 8, 9, 10, 11, 12, 13, 15, 16, 17
- §2.2 Navbar entry → Task 19
- §2.3 Tab structure → Task 7 (layout.html), Tasks 8/9/11/13/17 (panels)
- §3.1 New columns → Task 1
- §3.2 `letterboxd_imports` table → Task 2
- §3.3 Delete cascade → Task 17
- §3.4 Non-decisions (avatar storage, soft delete) → nothing to build (verified)
- §4.1 Profile → Task 8
- §4.2 Security (password, providers, sessions) → Tasks 9, 10, plus security.html in Task 9
- §4.3 Preferences → Tasks 11, 12
- §4.4 Data (import, export, clear) → Tasks 13, 14, 15, 16
- §4.5 Danger zone → Task 17
- §5.1 Import job → Task 14
- §5.2 Streaming exports → Task 15
- §5.3 Session revocation → Task 3 (+ consumed in 9, 10, 17)
- §5.4 Synchronous routes → Tasks 8, 11, 16, 17
- §6.1 Page shell → Task 7 (layout.html), Task 18 (pre-paint script update)
- §6.2 `.account-*` namespace → Task 5
- §6.3 Reuse of `.auth-*` + new `.btn-danger` + modal → Task 5
- §6.4 Typography → Task 5 (font-family uses `var(--font-serif)`, `var(--font-sans)`)
- §6.5 Visual review gate → Task 21 Step 5
- §7.1 Auth guard → Task 7 (`_require_user`)
- §7.2 CSRF → every POST decorated in Tasks 8–17
- §7.3 Rate limiting → Tasks 9, 13, 15, 17
- §7.4 Logging → lazy-format `logger.info("Account action: %s user=%s", ...)` in every handler
- §7.5 Metrics → deliberately **not expanded** into a dedicated task; every handler logs the action, and hooking `infra.metrics_groups.safe_emit` into those call sites is a small follow-up that doesn't add risk. **Gap noted — add to a follow-up issue** rather than expanding this already-large plan.
- §7.6 Tests → all test files created across the tasks
- §8 Files touched → every file in §8 appears in a task's Files block
- §9 Phase 2 readiness → not built (correct per spec)
- §10 Explicit non-goals → verified nothing in the plan violates these

**Placeholder scan:** No `TBD`, `TODO`, or "implement later" in the plan. Tasks have complete code.

**Type consistency check:**
- `revoke_user_sessions(redis_client, user_id, *, except_session_id=None)` — signature consistent across Tasks 3, 9, 10, 17
- `user_preferences.get_theme_preference` / `set_theme_preference` / `get_default_filters` / `set_default_filters` / `clear_default_filters` — consistent across Tasks 4, 7, 11, 12, 18
- `account_view` / `account_profile_save` / `account_password_change` / `account_sessions_revoke` / `account_preferences_save` / `account_filters_save` / `account_filters_clear` / `account_letterboxd_upload` / `account_import_progress` / `account_import_status` / `account_export_watched_csv` / `account_export_watched_json` / `account_watched_clear` / `account_delete` — endpoint names consistent across url_for references in templates and tests

**One follow-up noted (not blocking):** metrics emission (§7.5). Leave for a small follow-up issue after the feature lands.

---

Plan complete and saved to `docs/superpowers/plans/2026-04-14-account-settings.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
