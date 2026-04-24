# Persisted Watched Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the `Exclude watched` filter to user accounts, default it to `on` for new users, and ensure watched movies never resurface in future random discovery while the filter is enabled.

**Architecture:** Add a narrow account-preference path in the auth/session layer, synchronize that preference into navigation state when a user binds to a session and when filters are applied, and harden `MovieNavigator` to discard watched refs already sitting in the queue. Keep the watched toggle as a pure watched-list mutation; do not turn it into a navigation action.

**Tech Stack:** Python 3.11, Quart, MySQL-backed runtime schema, MySQL-backed navigation state, pytest, AsyncMock

---

## File Map

- `infra/runtime_schema.py`
  Adds the durable `users.exclude_watched_default` column and its additive runtime repair helper.
- `session/user_auth.py`
  Ensures both email and OAuth account creation explicitly write `exclude_watched_default=True`.
- `session/user_preferences.py`
  New narrow helper module for reading and updating the account preference.
- `infra/navigation_state.py`
  Adds a single versioned bind/sync entry point for attaching a user to the current navigation state with a seeded `exclude_watched` value.
- `nextreel/web/routes/shared.py`
  Adds a shared auth-session attach helper so login/register/OAuth do not duplicate preference sync logic.
- `nextreel/web/routes/auth.py`
  Replaces direct `set_user_id()` calls with the shared attach helper.
- `nextreel/web/routes/navigation.py`
  Persists `exclude_watched` to the user account on successful logged-in `Apply filters`.
- `nextreel/application/movie_navigator.py`
  Skips watched refs already queued before returning the next random discovery result.
- `tests/infra/test_runtime_schema.py`
  Covers schema/repair behavior for the new user column.
- `tests/session/test_user_auth.py`
  Covers account-creation inserts carrying the default preference.
- `tests/session/test_user_preferences.py`
  New unit tests for durable preference reads/writes.
- `tests/infra/test_navigation_state_components.py`
  Covers the versioned navigation-state bind helper.
- `tests/web/test_auth_routes.py`
  Covers auth routes binding the user and seeding `exclude_watched` from the durable account preference.
- `tests/web/test_routes_navigation.py`
  Covers `/filtered_movie` persisting the preference only on valid logged-in apply.
- `tests/application/test_movie_navigator_extended.py`
  Covers queue skipping/refill behavior when watched titles are already queued.
- `tests/movies/test_watched_filter.py`
  Keeps existing refill-merge coverage alongside the new queue-skip behavior.

## Phase 1: Durable Preference Plumbing

### Task 1: Add runtime schema support for `users.exclude_watched_default`

**Files:**
- Modify: `infra/runtime_schema.py`
- Test: `tests/infra/test_runtime_schema.py`

- [ ] **Step 1: Write the failing runtime schema tests**

Add these tests to `tests/infra/test_runtime_schema.py`:

```python
async def test_ensure_runtime_schema_creates_users_table_with_exclude_watched_default(
    mock_db_pool,
):
    users_sql = [
        s for s in _RUNTIME_SCHEMA_STATEMENTS
        if "CREATE TABLE IF NOT EXISTS users" in s
    ]
    assert len(users_sql) == 1
    assert "exclude_watched_default" in users_sql[0]
    assert "DEFAULT TRUE" in users_sql[0].upper()


async def test_ensure_users_exclude_watched_default_column_adds_when_missing(mock_db_pool):
    from infra.runtime_schema import ensure_users_exclude_watched_default_column

    await ensure_users_exclude_watched_default_column(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    alter_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ALTER TABLE users" in alter_query
    assert "ADD COLUMN exclude_watched_default BOOLEAN NOT NULL DEFAULT TRUE" in alter_query
```

- [ ] **Step 2: Run the targeted schema tests and confirm they fail**

Run: `pytest tests/infra/test_runtime_schema.py::test_ensure_runtime_schema_creates_users_table_with_exclude_watched_default tests/infra/test_runtime_schema.py::test_ensure_users_exclude_watched_default_column_adds_when_missing -v`

Expected: FAIL because the `users` table DDL does not include `exclude_watched_default` and `ensure_users_exclude_watched_default_column()` does not exist yet.

- [ ] **Step 3: Implement the additive schema change**

Update `infra/runtime_schema.py`:

```python
"""
CREATE TABLE IF NOT EXISTS users (
    user_id       CHAR(32) PRIMARY KEY,
    email         VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NULL,
    display_name  VARCHAR(100) NULL,
    auth_provider VARCHAR(20) NOT NULL DEFAULT 'email',
    oauth_sub     VARCHAR(255) NULL,
    exclude_watched_default BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    DATETIME(6) NOT NULL,
    updated_at    DATETIME(6) NOT NULL,
    UNIQUE KEY idx_users_email (email),
    UNIQUE KEY idx_users_oauth (auth_provider, oauth_sub)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
```

Add the repair helper and call it from `ensure_runtime_schema()`:

```python
async def ensure_users_exclude_watched_default_column(db_pool) -> None:
    await _ensure_column(
        db_pool,
        "users",
        "exclude_watched_default",
        """
        ALTER TABLE users
        ADD COLUMN exclude_watched_default BOOLEAN NOT NULL DEFAULT TRUE
        AFTER oauth_sub
        """,
    )


async def ensure_runtime_schema(db_pool) -> None:
    for statement in _RUNTIME_SCHEMA_STATEMENTS:
        await db_pool.execute(statement, fetch="none")
    await ensure_user_navigation_current_ref_column(db_pool)
    await ensure_movie_candidates_shuffle_key(db_pool)
    await ensure_movie_candidates_refreshed_at_index(db_pool)
    await ensure_movie_candidates_shuffle_key_index(db_pool)
    await ensure_movie_candidates_bucket_filter_index(db_pool)
    await ensure_popular_movies_cache_composite_index(db_pool)
    await ensure_user_navigation_user_id_column(db_pool)
    await ensure_users_exclude_watched_default_column(db_pool)
    logger.info("Runtime schema ensured")
```

- [ ] **Step 4: Run the schema tests and confirm they pass**

Run: `pytest tests/infra/test_runtime_schema.py::test_ensure_runtime_schema_creates_users_table_with_exclude_watched_default tests/infra/test_runtime_schema.py::test_ensure_users_exclude_watched_default_column_adds_when_missing -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add infra/runtime_schema.py tests/infra/test_runtime_schema.py
git commit -m "feat: add exclude-watched account preference schema"
```

### Task 2: Write the default preference during user creation

**Files:**
- Modify: `session/user_auth.py`
- Test: `tests/session/test_user_auth.py`

- [ ] **Step 1: Write the failing account-creation tests**

Add these tests to `tests/session/test_user_auth.py`:

```python
@pytest.mark.asyncio
async def test_register_user_sets_exclude_watched_default_true(mock_db_pool):
    mock_db_pool.execute.return_value = None

    await register_user(mock_db_pool, "user@example.com", "password123")

    insert_call = mock_db_pool.execute.call_args
    query = insert_call[0][0]
    params = insert_call[0][1]
    assert "exclude_watched_default" in query
    assert params[5] is True


@pytest.mark.asyncio
async def test_find_or_create_oauth_user_sets_exclude_watched_default_true(mock_db_pool):
    mock_db_pool.execute.side_effect = [None, None]

    await find_or_create_oauth_user(
        mock_db_pool,
        provider="google",
        oauth_sub="sub-123",
        email="user@example.com",
    )

    insert_call = mock_db_pool.execute.call_args_list[1]
    query = insert_call[0][0]
    params = insert_call[0][1]
    assert "exclude_watched_default" in query
    assert params[5] is True
```

- [ ] **Step 2: Run the targeted auth tests and confirm they fail**

Run: `pytest tests/session/test_user_auth.py::test_register_user_sets_exclude_watched_default_true tests/session/test_user_auth.py::test_find_or_create_oauth_user_sets_exclude_watched_default_true -v`

Expected: FAIL because the insert SQL does not yet mention `exclude_watched_default`.

- [ ] **Step 3: Update both user-creation paths to write the default explicitly**

Update `session/user_auth.py`:

```python
await db_pool.execute(
    """
    INSERT INTO users (user_id, email, password_hash, display_name,
                       auth_provider, exclude_watched_default, created_at, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """,
    [user_id, email.lower().strip(), password_hash, display_name, "email", True, now, now],
    fetch="none",
)
```

```python
await db_pool.execute(
    """
    INSERT INTO users (user_id, email, password_hash, display_name,
                       auth_provider, oauth_sub, exclude_watched_default, created_at, updated_at)
    VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s)
    """,
    [user_id, email.lower().strip(), display_name, provider, oauth_sub, True, now, now],
    fetch="none",
)
```

- [ ] **Step 4: Run the targeted auth tests and confirm they pass**

Run: `pytest tests/session/test_user_auth.py::test_register_user_sets_exclude_watched_default_true tests/session/test_user_auth.py::test_find_or_create_oauth_user_sets_exclude_watched_default_true -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add session/user_auth.py tests/session/test_user_auth.py
git commit -m "feat: default exclude-watched on new accounts"
```

### Task 3: Add the narrow durable preference helper

**Files:**
- Create: `session/user_preferences.py`
- Create: `tests/session/test_user_preferences.py`

- [ ] **Step 1: Write the failing preference-helper tests**

Create `tests/session/test_user_preferences.py`:

```python
from __future__ import annotations

import pytest

from session.user_preferences import (
    get_exclude_watched_default,
    set_exclude_watched_default,
)


@pytest.mark.asyncio
async def test_get_exclude_watched_default_returns_true_when_user_row_missing(mock_db_pool):
    mock_db_pool.execute.return_value = None

    result = await get_exclude_watched_default(mock_db_pool, "user-123")

    assert result is True


@pytest.mark.asyncio
async def test_get_exclude_watched_default_returns_false_from_row(mock_db_pool):
    mock_db_pool.execute.return_value = {"exclude_watched_default": 0}

    result = await get_exclude_watched_default(mock_db_pool, "user-123")

    assert result is False


@pytest.mark.asyncio
async def test_set_exclude_watched_default_updates_value_and_timestamp(mock_db_pool):
    mock_db_pool.execute.return_value = None

    await set_exclude_watched_default(mock_db_pool, "user-123", False)

    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "UPDATE users" in query
    assert params[0] is False
    assert params[2] == "user-123"
    assert call[1]["fetch"] == "none"
```

- [ ] **Step 2: Run the new preference-helper tests and confirm they fail**

Run: `pytest tests/session/test_user_preferences.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'session.user_preferences'`

- [ ] **Step 3: Implement the new helper module**

Create `session/user_preferences.py`:

```python
from __future__ import annotations

from infra.time_utils import utcnow


async def get_exclude_watched_default(db_pool, user_id: str) -> bool:
    row = await db_pool.execute(
        "SELECT exclude_watched_default FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )
    if not row:
        return True
    return bool(row.get("exclude_watched_default", True))


async def set_exclude_watched_default(db_pool, user_id: str, value: bool) -> None:
    await db_pool.execute(
        """
        UPDATE users
        SET exclude_watched_default = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [bool(value), utcnow(), user_id],
        fetch="none",
    )
```

- [ ] **Step 4: Run the helper tests and confirm they pass**

Run: `pytest tests/session/test_user_preferences.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add session/user_preferences.py tests/session/test_user_preferences.py
git commit -m "feat: add exclude-watched user preference helpers"
```

## Phase 2: Sync Durable Preference Into Session State

### Task 4: Add a versioned navigation-state bind path and reuse it from auth routes

**Files:**
- Modify: `infra/navigation_state.py`
- Modify: `nextreel/web/routes/shared.py`
- Modify: `nextreel/web/routes/auth.py`
- Test: `tests/infra/test_navigation_state_components.py`
- Test: `tests/web/test_auth_routes.py`

- [ ] **Step 1: Write the failing navigation-state bind test**

Add this test to `tests/infra/test_navigation_state_components.py`:

```python
class TestNavigationStateService:
    @pytest.mark.asyncio
    async def test_bind_user_overwrites_user_id_and_exclude_watched_filter(self):
        from infra.navigation_state import NavigationStateService, MutationResult

        repository = MagicMock()
        migration = MagicMock()
        service = NavigationStateService(repository=repository, migration=migration)
        state = _make_state()

        async def fake_mutate(session_id, mutator, legacy_session=None, current_state=None):
            working = current_state.clone()
            await mutator(working)
            return MutationResult(state=working, result=working, conflicted=False)

        service.mutate = AsyncMock(side_effect=fake_mutate)

        updated = await service.bind_user(state, "user-123", exclude_watched=False)

        assert updated.user_id == "user-123"
        assert updated.filters["exclude_watched"] is False
```

- [ ] **Step 2: Run the targeted navigation-state test and confirm it fails**

Run: `pytest tests/infra/test_navigation_state_components.py::TestNavigationStateService::test_bind_user_overwrites_user_id_and_exclude_watched_filter -v`

Expected: FAIL because `bind_user()` does not exist yet.

- [ ] **Step 3: Write the failing auth-route tests**

Add these tests to `tests/web/test_auth_routes.py`:

```python
class TestRegisterRoute:
    @pytest.mark.asyncio
    async def test_register_success_loads_preference_and_binds_navigation_state(self):
        with _make_auth_app() as (app, _manager):
            app.navigation_state_store.bind_user = AsyncMock(return_value=_nav_state(user_id="user-123"))

            with patch(
                "session.user_auth.get_user_by_email",
                AsyncMock(return_value=None),
            ), patch(
                "session.user_auth.hash_password_async",
                AsyncMock(return_value="hash"),
            ), patch(
                "session.user_auth.register_user",
                AsyncMock(return_value="user-123"),
            ), patch(
                "session.user_preferences.get_exclude_watched_default",
                AsyncMock(return_value=False),
            ) as get_pref:
                async with app.test_request_context(
                    "/register",
                    method="POST",
                    form={
                        "email": "person@example.com",
                        "password": "password123",
                        "confirm_password": "password123",
                    },
                    headers={"X-CSRFToken": "test-csrf-token"},
                ):
                    g.navigation_state = _nav_state()
                    response = await routes.register_submit()

        assert response.status_code == 303
        get_pref.assert_awaited_once()
        app.navigation_state_store.bind_user.assert_awaited_once()


class TestLoginRoute:
    @pytest.mark.asyncio
    async def test_login_success_loads_preference_and_binds_navigation_state(self):
        with _make_auth_app() as (app, _manager):
            app.navigation_state_store.bind_user = AsyncMock(return_value=_nav_state(user_id="user-123"))

            with patch(
                "session.user_auth.authenticate_user",
                AsyncMock(return_value="user-123"),
            ), patch(
                "session.user_preferences.get_exclude_watched_default",
                AsyncMock(return_value=True),
            ) as get_pref:
                async with app.test_request_context(
                    "/login",
                    method="POST",
                    form={"email": "person@example.com", "password": "password123"},
                    headers={"X-CSRFToken": "test-csrf-token"},
                ):
                    g.navigation_state = _nav_state()
                    response = await routes.login_submit()

        assert response.status_code == 303
        get_pref.assert_awaited_once()
        app.navigation_state_store.bind_user.assert_awaited_once()
```

- [ ] **Step 4: Run the targeted auth-route tests and confirm they fail**

Run: `pytest tests/web/test_auth_routes.py::TestRegisterRoute::test_register_success_loads_preference_and_binds_navigation_state tests/web/test_auth_routes.py::TestLoginRoute::test_login_success_loads_preference_and_binds_navigation_state -v`

Expected: FAIL because auth routes still call `set_user_id()` directly and never load the durable preference.

- [ ] **Step 5: Implement the versioned bind flow**

Add a new store/service method in `infra/navigation_state.py`:

```python
class NavigationStateService:
    async def bind_user(
        self,
        state: NavigationState,
        user_id: str,
        *,
        exclude_watched: bool,
    ) -> NavigationState | None:
        async def mutator(working: NavigationState) -> NavigationState:
            working.user_id = user_id
            working.filters = dict(working.filters)
            working.filters["exclude_watched"] = exclude_watched
            return working

        result = await self.mutate(
            state.session_id,
            mutator,
            current_state=state,
        )
        if result.conflicted:
            return None
        return result.state


class NavigationStateStore:
    async def bind_user(
        self,
        state: NavigationState,
        user_id: str,
        *,
        exclude_watched: bool,
    ) -> NavigationState | None:
        return await self.service.bind_user(
            state,
            user_id,
            exclude_watched=exclude_watched,
        )
```

Add a shared helper in `nextreel/web/routes/shared.py`:

```python
from session import user_preferences


async def _attach_user_to_current_session(user_id: str):
    state = _current_state()
    services = _services()
    exclude_watched = await user_preferences.get_exclude_watched_default(
        services.movie_manager.db_pool,
        user_id,
    )
    updated_state = await current_app.navigation_state_store.bind_user(
        state,
        user_id,
        exclude_watched=exclude_watched,
    )
    if updated_state is None:
        abort(409, description="Could not bind authenticated user to navigation state")
    g.navigation_state = updated_state
    return updated_state
```

Update `nextreel/web/routes/auth.py` to use the helper:

```python
from nextreel.web.routes.shared import _attach_user_to_current_session

state = await _attach_user_to_current_session(user_id)
logger.info("User %s logged in, session %s", user_id, state.session_id)
return redirect(url_for("main.home"), code=303)
```

- [ ] **Step 6: Run the targeted navigation/auth tests and confirm they pass**

Run: `pytest tests/infra/test_navigation_state_components.py::TestNavigationStateService::test_bind_user_overwrites_user_id_and_exclude_watched_filter tests/web/test_auth_routes.py::TestRegisterRoute::test_register_success_loads_preference_and_binds_navigation_state tests/web/test_auth_routes.py::TestLoginRoute::test_login_success_loads_preference_and_binds_navigation_state -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add infra/navigation_state.py nextreel/web/routes/shared.py nextreel/web/routes/auth.py tests/infra/test_navigation_state_components.py tests/web/test_auth_routes.py
git commit -m "feat: sync exclude-watched preference when users bind to sessions"
```

### Task 5: Persist the checkbox choice on successful `Apply filters`

**Files:**
- Modify: `nextreel/web/routes/navigation.py`
- Test: `tests/web/test_routes_navigation.py`

- [ ] **Step 1: Write the failing `/filtered_movie` persistence tests**

Add these tests to `tests/web/test_routes_navigation.py`:

```python
from types import SimpleNamespace
from quart import g
import routes


def _nav_state(*, user_id=None, filters=None):
    return SimpleNamespace(
        csrf_token="test-csrf-token",
        session_id="test-session-id",
        user_id=user_id,
        filters=filters or {},
    )


class TestFilteredMovieRoute:
    @pytest.mark.asyncio
    async def test_logged_in_apply_persists_exclude_watched_before_navigation(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst="tt1234567"))

        with patch(
            "nextreel.web.routes.navigation.set_exclude_watched_default",
            AsyncMock(),
        ) as set_pref:
            async with app.test_request_context(
                "/filtered_movie",
                method="POST",
                form={"year_min": "2000", "exclude_watched": "off"},
                headers={"X-CSRFToken": "test-csrf-token"},
            ):
                g.navigation_state = _nav_state(
                    user_id="user-123",
                    filters={"exclude_watched": True},
                )
                response = await routes.filtered_movie_endpoint()

        assert response.status_code == 303
        set_pref.assert_awaited_once_with(manager.db_pool, "user-123", False)
        manager.apply_filters.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_filters_do_not_persist_exclude_watched(self):
        app, manager = _make_app()

        with patch(
            "nextreel.web.routes.navigation.set_exclude_watched_default",
            AsyncMock(),
        ) as set_pref:
            async with app.test_request_context(
                "/filtered_movie",
                method="POST",
                form={"year_min": "2025", "year_max": "1900"},
                headers={"X-CSRFToken": "test-csrf-token"},
            ):
                g.navigation_state = _nav_state(user_id="user-123")
                response = await routes.filtered_movie_endpoint()

        assert response[1] == 400
        set_pref.assert_not_awaited()
        manager.apply_filters.assert_not_awaited()
```

- [ ] **Step 2: Run the targeted navigation-route tests and confirm they fail**

Run: `pytest tests/web/test_routes_navigation.py::TestFilteredMovieRoute::test_logged_in_apply_persists_exclude_watched_before_navigation tests/web/test_routes_navigation.py::TestFilteredMovieRoute::test_invalid_filters_do_not_persist_exclude_watched -v`

Expected: FAIL because `/filtered_movie` does not yet call `set_exclude_watched_default()`.

- [ ] **Step 3: Implement preference persistence on valid logged-in apply**

Update `nextreel/web/routes/navigation.py`:

```python
from session.user_preferences import set_exclude_watched_default


@bp.route("/filtered_movie", methods=["POST"])
async def filtered_movie_endpoint():
    movie_manager = _services().movie_manager
    state = _current_state()
    form_data = await request.form
    filters: FilterState = normalize_filters(form_data)
    validation_errors = validate_filters(filters)
    ...
    if validation_errors:
        ...

    if state.user_id:
        await set_exclude_watched_default(
            movie_manager.db_pool,
            state.user_id,
            bool(filters["exclude_watched"]),
        )

    outcome = await movie_manager.apply_filters(
        state,
        filters,
        legacy_session=_legacy_session(),
    )
```

- [ ] **Step 4: Run the targeted navigation-route tests and confirm they pass**

Run: `pytest tests/web/test_routes_navigation.py::TestFilteredMovieRoute::test_logged_in_apply_persists_exclude_watched_before_navigation tests/web/test_routes_navigation.py::TestFilteredMovieRoute::test_invalid_filters_do_not_persist_exclude_watched -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/navigation.py tests/web/test_routes_navigation.py
git commit -m "feat: persist exclude-watched preference on filter apply"
```

## Phase 3: Discovery Queue Hardening

### Task 6: Skip watched titles that were queued before the watched mutation

**Files:**
- Modify: `nextreel/application/movie_navigator.py`
- Test: `tests/application/test_movie_navigator_extended.py`
- Test: `tests/movies/test_watched_filter.py`

- [ ] **Step 1: Write the failing queued-title regression tests**

Add these tests to `tests/application/test_movie_navigator_extended.py`:

```python
@pytest.mark.asyncio
async def test_next_movie_skips_watched_refs_already_in_queue(nav_app):
    state = _state()
    state.user_id = "user-1"
    state.filters["exclude_watched"] = True
    state.queue = [
        {"tconst": "tt1", "title": "Watched", "slug": "watched"},
        {"tconst": "tt2", "title": "Fresh", "slug": "fresh"},
    ]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub()
    watched_store = MagicMock()
    watched_store.watched_tconsts = AsyncMock(return_value={"tt1"})
    navigator = MovieNavigator(candidates, store, watched_store=watched_store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1", current_state=state)

    assert outcome == NavigationOutcome(tconst="tt2")
    assert store.state.current_tconst == "tt2"


@pytest.mark.asyncio
async def test_next_movie_refills_when_all_prefetched_refs_are_now_watched(nav_app):
    state = _state()
    state.user_id = "user-1"
    state.filters["exclude_watched"] = True
    state.queue = [{"tconst": "tt1", "title": "Watched", "slug": "watched"}]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub(refs=[{"tconst": "tt2", "title": "Fresh", "slug": "fresh"}])
    watched_store = MagicMock()
    watched_store.watched_tconsts = AsyncMock(return_value={"tt1"})
    navigator = MovieNavigator(candidates, store, watched_store=watched_store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1", current_state=state)

    assert outcome == NavigationOutcome(tconst="tt2")
    assert candidates.fetch_candidate_refs_calls
```

- [ ] **Step 2: Run the targeted navigator tests and confirm they fail**

Run: `pytest tests/application/test_movie_navigator_extended.py::test_next_movie_skips_watched_refs_already_in_queue tests/application/test_movie_navigator_extended.py::test_next_movie_refills_when_all_prefetched_refs_are_now_watched -v`

Expected: FAIL because `next_movie()` currently pops the first queued ref without checking whether it became watched after prewarm.

- [ ] **Step 3: Implement queue skipping in the navigator**

Update `nextreel/application/movie_navigator.py`:

```python
class MovieNavigator:
    async def _watched_exclusion_set(self, state) -> set[str]:
        if (
            not self.watched_store
            or not getattr(state, "user_id", None)
            or not state.filters.get("exclude_watched", True)
        ):
            return set()
        return await self.watched_store.watched_tconsts(state.user_id)

    async def _pop_next_queue_ref(self, state) -> dict | None:
        watched = await self._watched_exclusion_set(state)
        while state.queue:
            next_ref = state.queue.pop(0)
            tconst = next_ref.get("tconst")
            if not tconst or tconst in watched:
                continue
            return next_ref
        return None

    async def next_movie(self, session_id: str, legacy_session=None, current_state=None):
        async def mutate(state):
            prefilled_empty_queue = False
            next_ref = None
            if state.future:
                next_ref = state.future.pop()
            else:
                if not state.queue:
                    await self._refill_queue(state, QUEUE_TARGET)
                    prefilled_empty_queue = True
                next_ref = await self._pop_next_queue_ref(state)
                if not next_ref and not prefilled_empty_queue:
                    await self._refill_queue(state, QUEUE_TARGET)
                    next_ref = await self._pop_next_queue_ref(state)
            ...
```

- [ ] **Step 4: Run the targeted navigator tests and confirm they pass**

Run: `pytest tests/application/test_movie_navigator_extended.py::test_next_movie_skips_watched_refs_already_in_queue tests/application/test_movie_navigator_extended.py::test_next_movie_refills_when_all_prefetched_refs_are_now_watched tests/movies/test_watched_filter.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add nextreel/application/movie_navigator.py tests/application/test_movie_navigator_extended.py tests/movies/test_watched_filter.py
git commit -m "fix: skip watched movies already queued for discovery"
```

## Phase 4: Final Verification

### Task 7: Run the focused feature suite and the CI-style Python test command

**Files:**
- Modify: none
- Test: `tests/infra/test_runtime_schema.py`
- Test: `tests/session/test_user_auth.py`
- Test: `tests/session/test_user_preferences.py`
- Test: `tests/infra/test_navigation_state_components.py`
- Test: `tests/web/test_auth_routes.py`
- Test: `tests/web/test_routes_navigation.py`
- Test: `tests/application/test_movie_navigator_extended.py`
- Test: `tests/movies/test_watched_filter.py`

- [ ] **Step 1: Run the focused feature suite**

Run:

```bash
pytest \
  tests/infra/test_runtime_schema.py \
  tests/session/test_user_auth.py \
  tests/session/test_user_preferences.py \
  tests/infra/test_navigation_state_components.py \
  tests/web/test_auth_routes.py \
  tests/web/test_routes_navigation.py \
  tests/application/test_movie_navigator_extended.py \
  tests/movies/test_watched_filter.py -v
```

Expected: PASS

- [ ] **Step 2: Run the repo’s CI-style pytest command**

Run:

```bash
pytest tests/ --cov=. --cov-report=term-missing --cov-report=xml --ignore=venv --ignore=node_modules
```

Expected: PASS

- [ ] **Step 3: If the CI-style run exposes unrelated flakes, rerun just the affected test file once to confirm whether the failure is deterministic**

Example:

```bash
pytest tests/web/test_auth_routes.py -v
```

Expected: Either PASS on rerun (flake) or the same failure twice (real regression to fix before merging).

- [ ] **Step 4: Commit any final test-only fixups from verification**

```bash
git add -A
git commit -m "test: finalize persisted watched exclusion coverage"
```

If Step 3 produced no file changes, skip this commit.

## Self-Review

- Spec coverage:
  - Durable account preference: Task 1, Task 2, Task 3
  - Auth/session seeding from account preference: Task 4
  - Persist only on successful apply: Task 5
  - Keep watched toggle non-navigating while excluding future discovery: Task 6
  - Queue hardening for stale prefetched refs: Task 6
- Placeholder scan:
  - No `TBD`, `TODO`, or “implement later” placeholders remain.
  - Every code-changing task includes concrete code snippets and exact commands.
- Type consistency:
  - Preference name is consistently `exclude_watched_default` in DB/helpers.
  - Session filter key is consistently `exclude_watched`.
  - Navigation-state sync entry point is consistently `bind_user(...)`.
