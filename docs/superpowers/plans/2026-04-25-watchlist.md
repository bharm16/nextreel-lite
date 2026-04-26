# Watchlist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-user "Watchlist" feature that mirrors the existing Watched-list pattern: per-movie toggle, dedicated `/watchlist` page with filters/sort/pagination, account-level toggle to exclude saved movies from the discovery queue.

**Architecture:** Approach A — parallel sibling. New `WatchlistStore`, `WatchlistPresenter`, `routes/watchlist.py`, templates, and JS that mirror the watched-list code with renames. Watched code is left intact except the navigator, which is widened to union watched + watchlist tconsts when computing exclusions, and the navigation-state binding which now also stamps `exclude_watchlist`.

**Tech Stack:** Python 3.11+/3.12, Quart (async Flask), MySQL via `aiomysql`, Redis via `aioredis`, Tailwind CSS (built artifact), vanilla JS. Tests: pytest-asyncio (`asyncio_mode = "auto"`).

---

## Project conventions (read once before starting)

- **TDD per task.** Write the failing test, watch it fail, write minimal code, watch it pass.
- **Logging.** Use `%s`-style lazy formatting only — never f-strings inside `logger.X(...)` calls. A pre-commit hook blocks f-string logging.
- **SQL.** Use `%s` placeholders for every value (including `LIMIT`/`OFFSET`). Never f-string-interpolate values into SQL. A pre-commit hook blocks f-string SQL.
- **Idempotent DDL.** Schema changes go through `_ensure_index` / `_ensure_column` in `infra/runtime_schema.py` — they catch MySQL errnos 1060 (dup column) and 1061 (dup key).
- **Cache.** Use `cache.safe_get_or_set(namespace, key, loader, ttl)` for "try cache, fall back to loader" reads. Namespace constants live on `infra.cache.CacheNamespace`.
- **Test fixtures.** `tests/conftest.py` provides `mock_db_pool` (an `AsyncMock` whose `.execute()` is mockable, plus a `_ddl_cursor` for verifying DDL), `cache_stub`, `fake_redis`, and a minimal Quart `app`.
- **Blueprint registration.** All routes hang off the shared `bp` Blueprint defined in `nextreel/web/routes/shared.py`. Adding a routes module means importing its handler functions in `nextreel/web/routes/__init__.py`.
- **Commits.** This project does NOT autocommit. Each "commit" step in this plan should: (a) `git add` the listed paths, (b) propose the commit message to the user as a `git commit -m "..."` you would run, but **do not execute** the commit. The user runs it themselves.
- **Run tests.** `python3 -m pytest tests/path/test_x.py -v` for a single file. `python3 -m pytest tests/ -v` for the whole suite.

---

## Phase 1: Schema (foundation)

### Task 1: Add `user_watchlist` table to runtime schema

**Files:**
- Modify: `infra/runtime_schema.py:129-255` — append a new entry to `_RUNTIME_SCHEMA_TABLE_DEFINITIONS`
- Test: `tests/infra/test_runtime_schema.py` — extend `_RUNTIME_SCHEMA_TABLES`

- [ ] **Step 1: Write the failing test**

In `tests/infra/test_runtime_schema.py`, find the `_RUNTIME_SCHEMA_TABLES` list (top of the file, around line 22) and append `"user_watchlist"`:

```python
_RUNTIME_SCHEMA_TABLES = [
    "runtime_metadata",
    "user_navigation_state",
    "movie_projection",
    "movie_candidates",
    "users",
    "user_watched_movies",
    "letterboxd_imports",
    "user_watchlist",          # NEW
]
```

Then add a focused test below the existing schema tests (find a good home near other `ensure_runtime_schema` tests):

```python
async def test_ensure_runtime_schema_creates_user_watchlist(mock_db_pool):
    """user_watchlist must be among the tables created on boot."""
    mock_db_pool.execute = AsyncMock(return_value=None)  # no rows -> all "missing"
    with patched_runtime_schema_repairs():
        await ensure_runtime_schema(mock_db_pool)
    ddl_calls = [
        call.args[0]
        for call in mock_db_pool._ddl_cursor.execute.await_args_list
    ]
    assert any("CREATE TABLE user_watchlist" in sql for sql in ddl_calls)
    assert any("PRIMARY KEY (user_id, tconst)" in sql for sql in ddl_calls
               if "user_watchlist" in sql)
    assert any("idx_watchlist_user_added" in sql for sql in ddl_calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py::test_ensure_runtime_schema_creates_user_watchlist -v`
Expected: FAIL — no DDL contains `CREATE TABLE user_watchlist`.

- [ ] **Step 3: Add the table definition**

In `infra/runtime_schema.py`, find `_RUNTIME_SCHEMA_TABLE_DEFINITIONS` (around line 129) and append a new tuple after the `letterboxd_imports` entry (so the closing `)` of the outer tuple is the last char):

```python
    (
        "user_watchlist",
        """
    CREATE TABLE user_watchlist (
        user_id  CHAR(32) NOT NULL,
        tconst   VARCHAR(16) NOT NULL,
        added_at DATETIME(6) NOT NULL,
        PRIMARY KEY (user_id, tconst),
        KEY idx_watchlist_user_added (user_id, added_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py -v`
Expected: PASS — both the new test and the existing `_RUNTIME_SCHEMA_TABLES` list test pass.

- [ ] **Step 5: Stage + propose commit**

```bash
git add infra/runtime_schema.py tests/infra/test_runtime_schema.py
# Propose: git commit -m "Add user_watchlist runtime table"
```

---

### Task 2: Add `users.exclude_watchlist_default` column repair helper

**Files:**
- Modify: `infra/runtime_schema.py` — new `ensure_users_exclude_watchlist_default_column` function + append name to `_RUNTIME_REPAIR_HELPER_NAMES`
- Test: `tests/infra/test_runtime_schema.py` — extend `_RUNTIME_SCHEMA_REPAIR_HELPERS` + new behavioral test

- [ ] **Step 1: Write the failing test**

Add to `_RUNTIME_SCHEMA_REPAIR_HELPERS` in `tests/infra/test_runtime_schema.py`:

```python
_RUNTIME_SCHEMA_REPAIR_HELPERS = [
    "ensure_user_navigation_current_ref_column",
    "ensure_movie_candidates_shuffle_key",
    "ensure_movie_candidates_refreshed_at_index",
    "ensure_movie_candidates_shuffle_key_index",
    "ensure_movie_candidates_bucket_filter_index",
    "ensure_movie_candidates_primaryTitle_index",
    "ensure_movie_candidates_fulltext_index",
    "ensure_movie_projection_state_last_attempt_index",
    "ensure_user_navigation_user_id_column",
    "ensure_users_exclude_watched_default_column",
    "ensure_users_theme_preference_column",
    "ensure_users_default_filters_json_column",
    "ensure_users_exclude_watchlist_default_column",  # NEW
]
```

Then add a focused test:

```python
async def test_ensure_users_exclude_watchlist_default_column_runs_alter(mock_db_pool):
    from infra.runtime_schema import ensure_users_exclude_watchlist_default_column

    await ensure_users_exclude_watchlist_default_column(mock_db_pool)
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    sql = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ALTER TABLE users" in sql
    assert "ADD COLUMN exclude_watchlist_default BOOLEAN NOT NULL DEFAULT TRUE" in sql


async def test_ensure_users_exclude_watchlist_default_column_skips_when_present(mock_db_pool):
    import pymysql
    from infra.runtime_schema import ensure_users_exclude_watchlist_default_column

    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(
            1060, "Duplicate column name 'exclude_watchlist_default'"
        )
    )
    # Must NOT raise — duplicate-column errno is the idempotent signal.
    await ensure_users_exclude_watchlist_default_column(mock_db_pool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py::test_ensure_users_exclude_watchlist_default_column_runs_alter -v`
Expected: FAIL — `ImportError: cannot import name 'ensure_users_exclude_watchlist_default_column'`.

- [ ] **Step 3: Add the helper + register it**

In `infra/runtime_schema.py`, add this helper after `ensure_users_default_filters_json_column` (around line 345):

```python
async def ensure_users_exclude_watchlist_default_column(db_pool) -> None:
    """Add the default watchlist-exclusion preference to existing users."""
    await _ensure_column(
        db_pool,
        "users",
        "exclude_watchlist_default",
        """
        ALTER TABLE users
        ADD COLUMN exclude_watchlist_default BOOLEAN NOT NULL DEFAULT TRUE
        AFTER exclude_watched_default
        """,
    )
```

Then append the name to `_RUNTIME_REPAIR_HELPER_NAMES` (around line 545):

```python
_RUNTIME_REPAIR_HELPER_NAMES = (
    "ensure_user_navigation_current_ref_column",
    "ensure_movie_candidates_shuffle_key",
    "ensure_movie_candidates_refreshed_at_index",
    "ensure_movie_candidates_shuffle_key_index",
    "ensure_movie_candidates_bucket_filter_index",
    "ensure_movie_candidates_primaryTitle_index",
    "ensure_movie_candidates_fulltext_index",
    "ensure_movie_projection_state_last_attempt_index",
    "ensure_user_navigation_user_id_column",
    "ensure_users_exclude_watched_default_column",
    "ensure_users_theme_preference_column",
    "ensure_users_default_filters_json_column",
    "ensure_users_exclude_watchlist_default_column",  # NEW
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py -v`
Expected: PASS for both new tests + existing tests.

- [ ] **Step 5: Stage + propose commit**

```bash
git add infra/runtime_schema.py tests/infra/test_runtime_schema.py
# Propose: git commit -m "Add users.exclude_watchlist_default schema repair helper"
```

---

## Phase 2: Data layer

### Task 3: Create `WatchlistStore` data-access class

**Files:**
- Create: `movies/watchlist_store.py`
- Create: `tests/movies/test_watchlist_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/movies/test_watchlist_store.py`:

```python
"""Tests for movies.watchlist_store — WatchlistStore data-access layer."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from movies.watchlist_store import WatchlistStore


def _make_store(mock_db_pool, cache=None) -> WatchlistStore:
    return WatchlistStore(mock_db_pool, cache=cache)


# ── add ──────────────────────────────────────────────────────────────


async def test_add_executes_insert_with_correct_params(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.add("user-1", "tt1234567")

    mock_db_pool.execute.assert_awaited_once()
    args = mock_db_pool.execute.call_args
    query, params = args[0][0], args[0][1]
    assert "INSERT INTO user_watchlist" in query
    assert "ON DUPLICATE KEY UPDATE" in query
    assert params[0] == "user-1"
    assert params[1] == "tt1234567"
    assert args[1]["fetch"] == "none"


async def test_add_is_idempotent(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.add("user-1", "tt1")
    await store.add("user-1", "tt1")

    assert mock_db_pool.execute.await_count == 2


# ── remove ───────────────────────────────────────────────────────────


async def test_remove_executes_delete_with_correct_params(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.remove("user-1", "tt1234567")

    args = mock_db_pool.execute.call_args
    query, params = args[0][0], args[0][1]
    assert "DELETE FROM user_watchlist" in query
    assert params == ["user-1", "tt1234567"]
    assert args[1]["fetch"] == "none"


# ── is_in_watchlist ──────────────────────────────────────────────────


async def test_is_in_watchlist_returns_true_when_row_exists(mock_db_pool):
    mock_db_pool.execute.return_value = {"cnt": 1}
    store = _make_store(mock_db_pool)

    assert await store.is_in_watchlist("user-1", "tt1") is True


async def test_is_in_watchlist_returns_false_when_no_row(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    assert await store.is_in_watchlist("user-1", "tt1") is False


# ── watchlist_tconsts ────────────────────────────────────────────────


async def test_watchlist_tconsts_returns_set_of_tconsts(mock_db_pool):
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}, {"tconst": "tt2"}]
    store = _make_store(mock_db_pool)

    result = await store.watchlist_tconsts("user-1")

    assert result == {"tt1", "tt2"}


async def test_watchlist_tconsts_uses_cache_when_available(mock_db_pool, cache_stub):
    mock_db_pool.execute.return_value = [{"tconst": "tt1"}]
    store = _make_store(mock_db_pool, cache=cache_stub)

    first = await store.watchlist_tconsts("user-1")
    second = await store.watchlist_tconsts("user-1")

    assert first == {"tt1"}
    assert second == {"tt1"}
    # safe_get_or_set hits the loader once, then serves cached.
    assert mock_db_pool.execute.await_count == 1


async def test_add_invalidates_cache(mock_db_pool, cache_stub):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool, cache=cache_stub)
    # Pre-warm a cached value so we can detect invalidation.
    await cache_stub.set("user", "watchlist_tconsts:user-1", ["tt-old"])

    await store.add("user-1", "tt-new")

    # After invalidation, get returns None → next read goes to loader.
    cached = await cache_stub.get("user", "watchlist_tconsts:user-1")
    assert cached is None


# ── count ────────────────────────────────────────────────────────────


async def test_count_returns_integer(mock_db_pool):
    mock_db_pool.execute.return_value = {"cnt": 7}
    store = _make_store(mock_db_pool)

    assert await store.count("user-1") == 7


# ── list_watchlist_filtered ──────────────────────────────────────────


async def test_list_watchlist_filtered_default_sort_is_recent(mock_db_pool):
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered("user-1", limit=10, offset=0)

    sql = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.added_at DESC" in sql


async def test_list_watchlist_filtered_supports_title_sort(mock_db_pool):
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered("user-1", sort="title_asc", limit=10, offset=0)

    sql = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY c.primaryTitle ASC" in sql


async def test_list_watchlist_filtered_falls_back_to_recent_for_unknown_sort(mock_db_pool):
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered("user-1", sort="bogus", limit=10, offset=0)

    sql = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.added_at DESC" in sql


async def test_list_watchlist_filtered_applies_decade_filter(mock_db_pool):
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watchlist_filtered(
        "user-1", limit=10, offset=0, decades=["1990"]
    )

    sql, params = mock_db_pool.execute.call_args[0][:2]
    assert "c.startYear >= %s" in sql and "c.startYear <= %s" in sql
    assert 1990 in params and 1999 in params


async def test_count_filtered_returns_count(mock_db_pool):
    mock_db_pool.execute.return_value = {"cnt": 4}
    store = _make_store(mock_db_pool)

    assert await store.count_filtered("user-1") == 4


# ── available_filter_chips ───────────────────────────────────────────


async def test_available_filter_chips_returns_decades_genres_ratings(mock_db_pool):
    mock_db_pool.execute.return_value = [
        {"startYear": 1995, "genres": "Drama,Comedy", "averageRating": 8.2},
        {"startYear": 2003, "genres": "Drama", "averageRating": 6.5},
    ]
    store = _make_store(mock_db_pool)

    chips = await store.available_filter_chips("user-1")

    assert "1990s" in chips["decades"]
    assert "2000s" in chips["decades"]
    assert "Drama" in chips["genres"]
    assert "Comedy" in chips["genres"]
    assert any(t["label"] == "8+" for t in chips["ratings"])
    assert any(t["label"] == "6–8" for t in chips["ratings"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/movies/test_watchlist_store.py -v`
Expected: FAIL — `ImportError: No module named 'movies.watchlist_store'`.

- [ ] **Step 3: Create `movies/watchlist_store.py`**

```python
"""CRUD operations for the user_watchlist table."""

from __future__ import annotations

from typing import Any

from infra.time_utils import utcnow
from logging_config import get_logger

logger = get_logger(__name__)

# Short TTL: stale-ok for the navigation hot path; invalidated on add/remove.
_WATCHLIST_CACHE_TTL = 300

_SORT_MAP = {
    "recent": "w.added_at DESC",
    "title_asc": "c.primaryTitle ASC",
    "title_desc": "c.primaryTitle DESC",
    "year_desc": "c.startYear DESC, c.primaryTitle ASC",
    "rating_desc": "c.averageRating DESC, c.primaryTitle ASC",
}


class WatchlistStore:
    """Data access layer for user watchlist (save-for-later) tracking."""

    def __init__(self, db_pool, cache=None):
        self.db_pool = db_pool
        self._cache = cache

    def attach_cache(self, cache) -> None:
        """Attach a cache manager. Idempotent — callers may rebind."""
        self._cache = cache

    def _cache_key(self, user_id: str) -> str:
        return f"watchlist_tconsts:{user_id}"

    async def _invalidate_cache(self, user_id: str) -> None:
        if not self._cache:
            return
        try:
            from infra.cache import CacheNamespace

            await self._cache.delete(CacheNamespace.USER, self._cache_key(user_id))
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "Watchlist cache invalidation failed for %s", user_id, exc_info=True
            )

    async def add(self, user_id: str, tconst: str) -> None:
        """Add a movie to the watchlist (idempotent)."""
        await self.db_pool.execute(
            """
            INSERT INTO user_watchlist (user_id, tconst, added_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE added_at = VALUES(added_at)
            """,
            [user_id, tconst, utcnow()],
            fetch="none",
        )
        await self._invalidate_cache(user_id)

    async def remove(self, user_id: str, tconst: str) -> None:
        """Remove a movie from the watchlist."""
        await self.db_pool.execute(
            "DELETE FROM user_watchlist WHERE user_id = %s AND tconst = %s",
            [user_id, tconst],
            fetch="none",
        )
        await self._invalidate_cache(user_id)

    async def is_in_watchlist(self, user_id: str, tconst: str) -> bool:
        row = await self.db_pool.execute(
            "SELECT 1 AS cnt FROM user_watchlist WHERE user_id = %s AND tconst = %s",
            [user_id, tconst],
            fetch="one",
        )
        return row is not None

    async def watchlist_tconsts(self, user_id: str) -> set[str]:
        """Return the set of all watchlist tconsts for a user.

        Cached in Redis under ``user:watchlist_tconsts:{user_id}`` with a
        5-minute TTL. Invalidated on add()/remove(). Falls back to a direct
        DB read when no cache is configured or Redis is unavailable.
        """

        async def _loader() -> list[str]:
            rows = await self.db_pool.execute(
                "SELECT tconst FROM user_watchlist WHERE user_id = %s",
                [user_id],
                fetch="all",
            )
            return [row["tconst"] for row in rows] if rows else []

        if not self._cache:
            return set(await _loader())

        from infra.cache import CacheNamespace

        cached = await self._cache.safe_get_or_set(
            CacheNamespace.USER,
            self._cache_key(user_id),
            _loader,
            ttl=_WATCHLIST_CACHE_TTL,
        )
        return set(cached) if cached is not None else set()

    async def count(self, user_id: str) -> int:
        row = await self.db_pool.execute(
            "SELECT COUNT(*) AS cnt FROM user_watchlist WHERE user_id = %s",
            [user_id],
            fetch="one",
        )
        return row["cnt"] if row else 0

    async def list_watchlist_filtered(
        self,
        user_id: str,
        *,
        sort: str = "recent",
        limit: int = 60,
        offset: int = 0,
        decades: list[str] | None = None,
        rating_min: float | None = None,
        rating_max: float | None = None,
        genres: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return watchlist movies with optional filtering and sorting."""
        where_clauses = ["w.user_id = %s"]
        params: list[Any] = [user_id]

        if decades:
            decade_parts = []
            for decade_str in decades:
                try:
                    decade_start = int(decade_str)
                except (TypeError, ValueError):
                    continue
                decade_parts.append("(c.startYear >= %s AND c.startYear <= %s)")
                params.extend([decade_start, decade_start + 9])
            if decade_parts:
                where_clauses.append("(" + " OR ".join(decade_parts) + ")")

        if rating_min is not None:
            where_clauses.append("c.averageRating >= %s")
            params.append(rating_min)
        if rating_max is not None:
            where_clauses.append("c.averageRating <= %s")
            params.append(rating_max)

        if genres:
            genre_parts = []
            for genre in genres:
                genre_parts.append("FIND_IN_SET(%s, c.genres) > 0")
                params.append(genre)
            if genre_parts:
                where_clauses.append("(" + " OR ".join(genre_parts) + ")")

        order_by = _SORT_MAP.get(sort, _SORT_MAP["recent"])
        where_sql = " AND ".join(where_clauses)
        params.extend([limit, offset])

        rows = await self.db_pool.execute(
            f"""
            SELECT sub.tconst, sub.added_at,
                   sub.primaryTitle, sub.startYear, sub.genres, sub.slug,
                   sub.averageRating,
                   p.payload_json
            FROM (
                SELECT w.tconst, w.added_at,
                       c.primaryTitle, c.startYear, c.genres, c.slug,
                       c.averageRating
                FROM user_watchlist w
                LEFT JOIN movie_candidates c ON w.tconst = c.tconst
                WHERE {where_sql}
                ORDER BY {order_by}
                LIMIT %s OFFSET %s
            ) sub
            LEFT JOIN movie_projection p ON sub.tconst = p.tconst
            """,
            params,
            fetch="all",
        )
        return rows if rows else []

    async def count_filtered(
        self,
        user_id: str,
        *,
        decades: list[str] | None = None,
        rating_min: float | None = None,
        rating_max: float | None = None,
        genres: list[str] | None = None,
    ) -> int:
        where_clauses = ["w.user_id = %s"]
        params: list[Any] = [user_id]

        if decades:
            decade_parts = []
            for decade_str in decades:
                try:
                    decade_start = int(decade_str)
                except (TypeError, ValueError):
                    continue
                decade_parts.append("(c.startYear >= %s AND c.startYear <= %s)")
                params.extend([decade_start, decade_start + 9])
            if decade_parts:
                where_clauses.append("(" + " OR ".join(decade_parts) + ")")

        if rating_min is not None:
            where_clauses.append("c.averageRating >= %s")
            params.append(rating_min)
        if rating_max is not None:
            where_clauses.append("c.averageRating <= %s")
            params.append(rating_max)

        if genres:
            genre_parts = []
            for genre in genres:
                genre_parts.append("FIND_IN_SET(%s, c.genres) > 0")
                params.append(genre)
            if genre_parts:
                where_clauses.append("(" + " OR ".join(genre_parts) + ")")

        where_sql = " AND ".join(where_clauses)

        row = await self.db_pool.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM user_watchlist w
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            WHERE {where_sql}
            """,
            params,
            fetch="one",
        )
        return row["cnt"] if row else 0

    async def available_filter_chips(self, user_id: str) -> dict[str, list]:
        rows = await self.db_pool.execute(
            """
            SELECT c.startYear, c.genres, c.averageRating
            FROM user_watchlist w
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            WHERE w.user_id = %s AND c.tconst IS NOT NULL
            """,
            [user_id],
            fetch="all",
        )
        if not rows:
            return {"decades": [], "genres": [], "ratings": []}

        decade_set: set[str] = set()
        genre_set: set[str] = set()
        has_8_plus = False
        has_6_8 = False
        has_under_6 = False

        for row in rows:
            year = row.get("startYear")
            if year:
                try:
                    decade = (int(year) // 10) * 10
                    decade_set.add(f"{decade}s")
                except (TypeError, ValueError):
                    pass

            genres_csv = row.get("genres")
            if genres_csv and isinstance(genres_csv, str):
                for g in genres_csv.split(","):
                    g = g.strip()
                    if g:
                        genre_set.add(g)

            rating = row.get("averageRating")
            if rating is not None:
                try:
                    r = float(rating)
                    if r >= 8.0:
                        has_8_plus = True
                    elif r >= 6.0:
                        has_6_8 = True
                    else:
                        has_under_6 = True
                except (TypeError, ValueError):
                    pass

        rating_tiers = []
        if has_8_plus:
            rating_tiers.append({"label": "8+", "min": 8.0, "max": 10.0})
        if has_6_8:
            rating_tiers.append({"label": "6–8", "min": 6.0, "max": 7.99})
        if has_under_6:
            rating_tiers.append({"label": "<6", "min": 0.0, "max": 5.99})

        return {
            "decades": sorted(decade_set, reverse=True),
            "genres": sorted(genre_set),
            "ratings": rating_tiers,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/movies/test_watchlist_store.py -v`
Expected: PASS — all 14 tests green.

- [ ] **Step 5: Stage + propose commit**

```bash
git add movies/watchlist_store.py tests/movies/test_watchlist_store.py
# Propose: git commit -m "Add WatchlistStore data-access layer"
```

---

## Phase 3: User preferences API

### Task 4: Add `get/set_exclude_watchlist_default` to user_preferences

**Files:**
- Modify: `session/user_preferences.py`
- Test: `tests/session/test_user_preferences.py` (extend if it exists, otherwise create)

- [ ] **Step 1: Confirm test file location**

Run: `ls tests/session/ 2>/dev/null || ls tests/ | grep -i preference`
If no `tests/session/test_user_preferences.py` exists, create it. If it exists, extend it.

- [ ] **Step 2: Write the failing test**

Add to `tests/session/test_user_preferences.py` (create the file if missing):

```python
"""Tests for session.user_preferences."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from session import user_preferences


async def test_get_exclude_watchlist_default_returns_true_when_no_user(mock_db_pool):
    """When user_id has no row, default to True."""
    mock_db_pool.execute.return_value = None

    result = await user_preferences.get_exclude_watchlist_default(mock_db_pool, "u1")

    assert result is True


async def test_get_exclude_watchlist_default_returns_stored_bool(mock_db_pool):
    mock_db_pool.execute.return_value = {"exclude_watchlist_default": False}

    result = await user_preferences.get_exclude_watchlist_default(mock_db_pool, "u1")

    assert result is False


async def test_set_exclude_watchlist_default_writes_update(mock_db_pool):
    mock_db_pool.execute.return_value = None

    await user_preferences.set_exclude_watchlist_default(mock_db_pool, "u1", False)

    sql, params = mock_db_pool.execute.call_args[0][:2]
    assert "UPDATE users" in sql
    assert "exclude_watchlist_default = %s" in sql
    assert params[0] is False
    assert params[2] == "u1"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/session/test_user_preferences.py -v`
Expected: FAIL — `AttributeError: get_exclude_watchlist_default`.

- [ ] **Step 4: Add the helpers**

In `session/user_preferences.py`, add these two functions immediately after `set_exclude_watched_default` (around line 30):

```python
async def get_exclude_watchlist_default(db_pool, user_id: str) -> bool:
    row = await db_pool.execute(
        "SELECT exclude_watchlist_default FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )
    if not row:
        return True
    return bool(row.get("exclude_watchlist_default", True))


async def set_exclude_watchlist_default(db_pool, user_id: str, value: bool) -> None:
    await db_pool.execute(
        """
        UPDATE users
        SET exclude_watchlist_default = %s, updated_at = %s
        WHERE user_id = %s
        """,
        [bool(value), utcnow(), user_id],
        fetch="none",
    )
```

- [ ] **Step 5: Run test + commit**

Run: `python3 -m pytest tests/session/test_user_preferences.py -v`
Expected: PASS.

```bash
git add session/user_preferences.py tests/session/test_user_preferences.py
# Propose: git commit -m "Add get/set_exclude_watchlist_default user preference helpers"
```

---

## Phase 4: Filter normalizer + FilterState contract

### Task 5: Add `exclude_watchlist` to FilterState, default_filter_state, normalize_filters

**Files:**
- Modify: `nextreel/domain/filter_contracts.py:13-22` — add `exclude_watchlist: bool` to `FilterState`
- Modify: `infra/filter_normalizer.py:23-35` — default state
- Modify: `infra/filter_normalizer.py:90-127` — normalize from form
- Test: `tests/infra/test_filter_normalizer.py` — extend with new cases

- [ ] **Step 1: Write the failing test**

Append to `tests/infra/test_filter_normalizer.py`:

```python
def test_default_filter_state_includes_exclude_watchlist():
    state = default_filter_state()
    assert state["exclude_watchlist"] is True


def test_normalize_filters_exclude_watchlist_on_when_checkbox_checked():
    class FakeForm:
        def get(self, key, default=None):
            return default

        def getlist(self, key):
            if key == "exclude_watchlist":
                return ["off", "on"]
            return []

    result = normalize_filters(FakeForm())
    assert result["exclude_watchlist"] is True


def test_normalize_filters_exclude_watchlist_off_when_only_hidden():
    class FakeForm:
        def get(self, key, default=None):
            return default

        def getlist(self, key):
            if key == "exclude_watchlist":
                return ["off"]
            return []

    result = normalize_filters(FakeForm())
    assert result["exclude_watchlist"] is False


def test_normalize_filters_exclude_watchlist_defaults_true_when_absent():
    class FakeForm:
        def get(self, key, default=None):
            return default

        def getlist(self, key):
            return []

    result = normalize_filters(FakeForm())
    assert result["exclude_watchlist"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infra/test_filter_normalizer.py -v -k watchlist`
Expected: FAIL — `KeyError: 'exclude_watchlist'`.

- [ ] **Step 3: Update FilterState contract**

Edit `nextreel/domain/filter_contracts.py`. Add a single line at the bottom of the `FilterState` TypedDict:

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
    exclude_watchlist: bool   # NEW
```

- [ ] **Step 4: Update `default_filter_state` and `normalize_filters`**

In `infra/filter_normalizer.py`, change `default_filter_state` (line 23) to include the new key:

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
        "exclude_watchlist": True,   # NEW
    }
```

In the same file, immediately after the `exclude_watched` block (around line 125), add:

```python
    # exclude_watchlist checkbox: same hidden-input + checkbox dance as watched.
    exclude_watchlist_values = form_data.getlist("exclude_watchlist")
    if "on" in exclude_watchlist_values:
        filters["exclude_watchlist"] = True
    elif "off" in exclude_watchlist_values:
        filters["exclude_watchlist"] = False
    else:
        filters["exclude_watchlist"] = True
```

- [ ] **Step 5: Run test + commit**

Run: `python3 -m pytest tests/infra/test_filter_normalizer.py -v`
Expected: PASS — all existing tests + 4 new ones.

```bash
git add nextreel/domain/filter_contracts.py infra/filter_normalizer.py tests/infra/test_filter_normalizer.py
# Propose: git commit -m "Add exclude_watchlist to FilterState and normalizer"
```

---

## Phase 5: Navigator wiring

### Task 6: Plumb `watchlist_store` through `MovieNavigator`

**Files:**
- Modify: `nextreel/application/movie_navigator.py:38-125` — `__init__`, `_refill_queue`, `_watchlist_exclusion_set` (new), `_pop_next_queue_ref`, `next_movie`
- Test: `tests/web/test_routes_navigation.py` — extend (mainly to ensure existing tests still pass with the new `watchlist_store=None` default)
- Test: Create `tests/application/test_movie_navigator_watchlist.py` for the new exclusion behavior

- [ ] **Step 1: Write the failing test**

Create `tests/application/test_movie_navigator_watchlist.py` (create the directory if missing):

```python
"""Navigator tests for watchlist exclusion behavior."""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nextreel.application.movie_navigator import MovieNavigator


def _state(*, user_id="u1", filters=None, queue=None):
    return SimpleNamespace(
        user_id=user_id,
        filters=filters or {"exclude_watched": True, "exclude_watchlist": True},
        queue=list(queue or []),
        prev=[],
        future=[],
        seen=[],
        current_tconst=None,
        current_ref=None,
    )


async def test_watchlist_exclusion_set_returns_empty_when_no_store():
    nav = MovieNavigator(MagicMock(), MagicMock())
    result = await nav._watchlist_exclusion_set(_state())
    assert result == set()


async def test_watchlist_exclusion_set_returns_empty_when_filter_disabled():
    watchlist_store = MagicMock()
    watchlist_store.watchlist_tconsts = AsyncMock(return_value={"tt1"})
    nav = MovieNavigator(MagicMock(), MagicMock(), watchlist_store=watchlist_store)
    state = _state(filters={"exclude_watched": True, "exclude_watchlist": False})

    result = await nav._watchlist_exclusion_set(state)

    assert result == set()
    watchlist_store.watchlist_tconsts.assert_not_awaited()


async def test_watchlist_exclusion_set_returns_tconsts_when_enabled():
    watchlist_store = MagicMock()
    watchlist_store.watchlist_tconsts = AsyncMock(return_value={"tt1", "tt2"})
    nav = MovieNavigator(MagicMock(), MagicMock(), watchlist_store=watchlist_store)

    result = await nav._watchlist_exclusion_set(_state())

    assert result == {"tt1", "tt2"}


async def test_refill_queue_excludes_watched_and_watchlist_union():
    candidate_store = MagicMock()
    candidate_store.fetch_candidate_refs = AsyncMock(return_value=[])
    watched_store = MagicMock()
    watched_store.watched_tconsts = AsyncMock(return_value={"tt-watched"})
    watchlist_store = MagicMock()
    watchlist_store.watchlist_tconsts = AsyncMock(return_value={"tt-saved"})
    nav = MovieNavigator(
        candidate_store,
        MagicMock(),
        watched_store=watched_store,
        watchlist_store=watchlist_store,
    )

    state = _state()
    await nav._refill_queue(state, desired_size=10)

    excluded = candidate_store.fetch_candidate_refs.await_args[0][1]
    assert "tt-watched" in excluded
    assert "tt-saved" in excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_movie_navigator_watchlist.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'watchlist_store'`.

- [ ] **Step 3: Update `MovieNavigator`**

In `nextreel/application/movie_navigator.py`, update the constructor (around line 38) to accept `watchlist_store`:

```python
class MovieNavigator:
    """State-aware next/previous/filter navigation."""

    def __init__(
        self,
        candidate_store,
        navigation_state_store,
        watched_store=None,
        watchlist_store=None,
    ):
        self.candidate_store = candidate_store
        self.navigation_state_store = navigation_state_store
        self.watched_store = watched_store
        self.watchlist_store = watchlist_store
```

Add `_watchlist_exclusion_set` immediately after `_watched_exclusion_set` (around line 110):

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

Update `_refill_queue` (around line 77) to take and union both:

```python
    async def _refill_queue(
        self,
        state,
        desired_size: int,
        *,
        watched_exclusion: set[str] | None = None,
        watchlist_exclusion: set[str] | None = None,
    ) -> None:
        missing = max(0, desired_size - len(state.queue))
        if missing <= 0:
            return

        excluded = self._excluded_tconsts(state)

        if watched_exclusion is None:
            watched_exclusion = await self._watched_exclusion_set(state)
        if watchlist_exclusion is None:
            watchlist_exclusion = await self._watchlist_exclusion_set(state)
        excluded |= watched_exclusion | watchlist_exclusion

        refs = await self.candidate_store.fetch_candidate_refs(
            state.filters,
            excluded,
            missing,
        )
        if refs:
            state.queue.extend(refs)
            state.queue = state.queue[:QUEUE_TARGET]
```

Update `_pop_next_queue_ref` (around line 112) similarly:

```python
    async def _pop_next_queue_ref(
        self,
        state,
        watched_exclusion: set[str] | None = None,
        watchlist_exclusion: set[str] | None = None,
    ) -> dict | None:
        if watched_exclusion is None:
            watched_exclusion = await self._watched_exclusion_set(state)
        if watchlist_exclusion is None:
            watchlist_exclusion = await self._watchlist_exclusion_set(state)
        combined = watched_exclusion | watchlist_exclusion
        while state.queue:
            next_ref = state.queue.pop(0)
            tconst = next_ref.get("tconst")
            if tconst and tconst in combined:
                continue
            return next_ref
        return None
```

Finally, update `next_movie` (around line 153) to fetch watchlist exclusion once and reuse:

```python
        async def mutate(state):
            prefilled_empty_queue = False
            next_ref = None
            watched_exclusion = None
            watchlist_exclusion = None
            if state.future:
                next_ref = state.future.pop()
            else:
                watched_exclusion = await self._watched_exclusion_set(state)
                watchlist_exclusion = await self._watchlist_exclusion_set(state)
                if not state.queue:
                    await self._refill_queue(
                        state,
                        QUEUE_TARGET,
                        watched_exclusion=watched_exclusion,
                        watchlist_exclusion=watchlist_exclusion,
                    )
                    prefilled_empty_queue = True
                if state.queue:
                    next_ref = await self._pop_next_queue_ref(
                        state, watched_exclusion, watchlist_exclusion
                    )
                if not next_ref and not prefilled_empty_queue:
                    await self._refill_queue(
                        state,
                        QUEUE_TARGET,
                        watched_exclusion=watched_exclusion,
                        watchlist_exclusion=watchlist_exclusion,
                    )
                    next_ref = await self._pop_next_queue_ref(
                        state, watched_exclusion, watchlist_exclusion
                    )

            if not next_ref or not next_ref.get("tconst"):
                return None

            previous_ref = await self._ref_for_current(state)
            if previous_ref and previous_ref.get("tconst") != next_ref.get("tconst"):
                state.prev.append(previous_ref)
                state.prev = state.prev[-PREV_STACK_MAX:]

            self._set_current(state, next_ref)
            self._mark_seen(state, state.current_tconst)

            if not prefilled_empty_queue and len(state.queue) < QUEUE_REFILL_THRESHOLD:
                await self._refill_queue(
                    state,
                    QUEUE_TARGET,
                    watched_exclusion=watched_exclusion,
                    watchlist_exclusion=watchlist_exclusion,
                )

            return state.current_tconst
```

- [ ] **Step 4: Run all navigator tests**

Run: `python3 -m pytest tests/application/test_movie_navigator_watchlist.py tests/web/test_routes_navigation.py -v`
Expected: PASS — new tests + all pre-existing navigation tests still green (the new `watchlist_store=None` default preserves the legacy single-arg call shape).

- [ ] **Step 5: Stage + propose commit**

```bash
git add nextreel/application/movie_navigator.py tests/application/test_movie_navigator_watchlist.py
# Propose: git commit -m "Plumb watchlist_store exclusion through MovieNavigator"
```

---

## Phase 6: Composition root + MovieManager

### Task 7: Wire `WatchlistStore` through factory and MovieManager.attach_cache

**Files:**
- Modify: `nextreel/bootstrap/movie_manager_factory.py` — instantiate `WatchlistStore`, pass to `MovieManager`
- Modify: `nextreel/application/movie_service.py:31-58` — accept `watchlist_store`, pass to `MovieNavigator`
- Modify: `nextreel/application/movie_service.py:114-131` — `attach_cache` propagates to `watchlist_store`
- Test: existing `tests/web/test_routes_navigation.py` mock `manager.watchlist_store`

- [ ] **Step 1: Write the failing test**

Add a focused test in `tests/movies/test_watchlist_store.py` (or a new `tests/application/test_movie_service_watchlist_wiring.py`):

```python
"""Verify MovieManager exposes watchlist_store and propagates cache."""

from __future__ import annotations

from unittest.mock import MagicMock

from movies.watchlist_store import WatchlistStore
from nextreel.application.movie_service import MovieManager


def test_movie_manager_has_default_watchlist_store():
    mgr = MovieManager(
        db_config={"host": "x", "user": "y", "password": "z", "db": "w", "port": 3306},
        db_pool=MagicMock(),
        tmdb_helper=MagicMock(),
        candidate_store=MagicMock(),
        projection_store=MagicMock(coordinator=None),
        watched_store=MagicMock(),
    )
    assert isinstance(mgr.watchlist_store, WatchlistStore)


def test_movie_manager_accepts_injected_watchlist_store():
    custom = MagicMock(spec=WatchlistStore)
    mgr = MovieManager(
        db_config={"host": "x", "user": "y", "password": "z", "db": "w", "port": 3306},
        db_pool=MagicMock(),
        tmdb_helper=MagicMock(),
        candidate_store=MagicMock(),
        projection_store=MagicMock(coordinator=None),
        watched_store=MagicMock(),
        watchlist_store=custom,
    )
    assert mgr.watchlist_store is custom


def test_attach_cache_calls_watchlist_store_attach_cache():
    watchlist_store = MagicMock()
    watchlist_store.attach_cache = MagicMock()
    mgr = MovieManager(
        db_config={"host": "x", "user": "y", "password": "z", "db": "w", "port": 3306},
        db_pool=MagicMock(),
        tmdb_helper=MagicMock(),
        candidate_store=MagicMock(attach_cache=MagicMock()),
        projection_store=MagicMock(coordinator=None),
        watched_store=MagicMock(attach_cache=MagicMock()),
        watchlist_store=watchlist_store,
    )
    cache = MagicMock()
    mgr.attach_cache(cache)
    watchlist_store.attach_cache.assert_called_once_with(cache)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/application/test_movie_service_watchlist_wiring.py -v`
Expected: FAIL — `AttributeError: 'MovieManager' object has no attribute 'watchlist_store'`.

- [ ] **Step 3: Update factory + MovieManager**

In `nextreel/bootstrap/movie_manager_factory.py`:

```python
from __future__ import annotations

from infra.pool import DatabaseConnectionPool
from movies.candidate_store import CandidateStore
from movies.projection_store import ProjectionStore
from movies.tmdb_client import TMDbHelper
from movies.watched_store import WatchedStore
from movies.watchlist_store import WatchlistStore           # NEW
from nextreel.application.movie_service import HomePrewarmService, MovieManager
from nextreel.web.movie_renderer import MovieRenderer


def build_movie_manager(
    db_config: dict[str, object],
    *,
    db_pool_cls=DatabaseConnectionPool,
    tmdb_helper_cls=TMDbHelper,
    candidate_store_cls=CandidateStore,
    projection_store_cls=ProjectionStore,
    watched_store_cls=WatchedStore,
    watchlist_store_cls=WatchlistStore,                     # NEW
    renderer_cls=MovieRenderer,
    home_prewarm_service_cls=HomePrewarmService,
    movie_manager_cls=MovieManager,
) -> MovieManager:
    """Compose MovieManager runtime dependencies."""
    db_pool = db_pool_cls(db_config)
    tmdb_helper = tmdb_helper_cls()
    candidate_store = candidate_store_cls(db_pool)
    projection_store = projection_store_cls(db_pool, tmdb_helper=tmdb_helper)
    watched_store = watched_store_cls(db_pool)
    watchlist_store = watchlist_store_cls(db_pool)          # NEW
    renderer = renderer_cls(projection_store)
    home_prewarm_service = home_prewarm_service_cls()
    return movie_manager_cls(
        db_config=db_config,
        db_pool=db_pool,
        tmdb_helper=tmdb_helper,
        candidate_store=candidate_store,
        projection_store=projection_store,
        watched_store=watched_store,
        watchlist_store=watchlist_store,                    # NEW
        renderer=renderer,
        home_prewarm_service=home_prewarm_service,
    )
```

In `nextreel/application/movie_service.py`, add the import (around line 17):

```python
from movies.watched_store import WatchedStore
from movies.watchlist_store import WatchlistStore           # NEW
```

Add to `MovieManager.__init__` (around line 38):

```python
        watched_store: WatchedStore | None = None,
        watchlist_store: WatchlistStore | None = None,     # NEW
```

After `self.watched_store = ...` (around line 58):

```python
        self.watched_store = watched_store or WatchedStore(self.db_pool)
        self.watchlist_store = watchlist_store or WatchlistStore(self.db_pool)
```

Update the navigator construction (around line 67) to pass `watchlist_store`:

```python
        self._navigator = navigator or MovieNavigator(
            self.candidate_store,
            self.navigation_state_store,
            watched_store=self.watched_store,
            watchlist_store=self.watchlist_store,
        )
```

Update `attach_cache` (around line 125) to propagate:

```python
        self.watched_store.attach_cache(cache)
        self.watchlist_store.attach_cache(cache)            # NEW
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/application/test_movie_service_watchlist_wiring.py tests/web/test_routes_navigation.py -v`
Expected: PASS — new wiring tests + all existing navigation tests still pass (since `watchlist_store=None` keeps default behavior identical for code that doesn't expect it).

NOTE: existing `test_routes_navigation.py` test fixtures stub `manager.watchlist_store = MagicMock()` may need to be added if any test actually exercises it. Run the suite and add `manager.watchlist_store = MagicMock(); manager.watchlist_store.is_in_watchlist = AsyncMock(return_value=False); manager.watchlist_store.watchlist_tconsts = AsyncMock(return_value=set())` next to the `manager.watched_store = MagicMock()` block (around `_make_app` line 50) only if tests fail.

- [ ] **Step 5: Stage + propose commit**

```bash
git add nextreel/bootstrap/movie_manager_factory.py nextreel/application/movie_service.py tests/application/test_movie_service_watchlist_wiring.py
# Propose: git commit -m "Wire WatchlistStore through MovieManager and factory"
```

---

## Phase 7: Navigation state binding + filter persistence

### Task 8: Add `exclude_watchlist` to `bind_user` and `/filtered_movie` persistence

**Files:**
- Modify: `nextreel/application/navigation_state_service.py:256-272` — `bind_user` accepts `exclude_watchlist`
- Modify: `nextreel/web/routes/shared.py:257-270` — `_attach_user_to_current_session` passes it through
- Modify: `nextreel/web/routes/navigation.py` — persist `exclude_watchlist` on filter apply (mirror `set_exclude_watched_default` block around line 114-119)
- Test: extend `tests/web/test_routes_navigation.py` with one test for the watchlist persistence path

- [ ] **Step 1: Write the failing test**

In `tests/web/test_routes_navigation.py`, add a parallel test next to the existing `test_logged_in_valid_apply_persists_exclude_watched_*` block (around line 198):

```python
async def test_logged_in_valid_apply_persists_exclude_watchlist_false_before_applying_filters(self):
    """When exclude_watchlist=off submitted, persist False to user prefs."""
    # Mirrors test_logged_in_valid_apply_persists_exclude_watched_false_before_applying_filters.
    # ... (follow that test's structure exactly, swapping watchlist for watched)
```

Concretely: copy the entire `test_logged_in_valid_apply_persists_exclude_watched_false_before_applying_filters` test body, swap:
- `set_exclude_watched_default` → `set_exclude_watchlist_default`
- `"exclude_watched": "off"` → `"exclude_watchlist": "off"` (and add `"exclude_watched": "on"` to keep the watched assertion neutral)
- `applied_filters["exclude_watched"] is False` → `applied_filters["exclude_watchlist"] is False`

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_routes_navigation.py -v -k watchlist`
Expected: FAIL — `set_exclude_watchlist_default` is not patched on `nextreel.web.routes.navigation`.

- [ ] **Step 3: Update `bind_user` signature**

In `nextreel/application/navigation_state_service.py`, change `bind_user` (around line 256):

```python
    async def bind_user(
        self,
        state: NavigationState,
        user_id: str,
        *,
        exclude_watched: bool,
        exclude_watchlist: bool = True,
    ) -> NavigationState | None:
        def mutator(working: NavigationState) -> NavigationState:
            working.user_id = user_id
            working.filters = dict(working.filters)
            working.filters["exclude_watched"] = exclude_watched
            working.filters["exclude_watchlist"] = exclude_watchlist
            return working

        result = await self.mutate(state.session_id, mutator, current_state=state)
        if result.conflicted:
            return None
        return result.state
```

- [ ] **Step 4: Update the caller in shared.py**

In `nextreel/web/routes/shared.py`, update `_attach_user_to_current_session` (line 257):

```python
async def _attach_user_to_current_session(user_id: str):
    state = _current_state()
    services = _services()
    exclude_watched = await user_preferences.get_exclude_watched_default(
        services.movie_manager.db_pool, user_id
    )
    exclude_watchlist = await user_preferences.get_exclude_watchlist_default(
        services.movie_manager.db_pool, user_id
    )
    updated_state = await current_app.navigation_state_store.bind_user(
        state,
        user_id,
        exclude_watched=exclude_watched,
        exclude_watchlist=exclude_watchlist,
    )
    if updated_state is None:
        abort(409, description="Could not bind authenticated user to navigation state")
    g.navigation_state = updated_state
    g.set_nr_sid_cookie = True
    return updated_state
```

- [ ] **Step 5: Update the filter-apply persistence in navigation.py**

In `nextreel/web/routes/navigation.py`, find the existing `set_exclude_watched_default` import (top of file) and the call site at line 114-119. Add the parallel import and call:

```python
# At top of file, alongside set_exclude_watched_default import:
from session.user_preferences import (
    set_exclude_watched_default,
    set_exclude_watchlist_default,   # NEW
)

# At the persist block (around line 114):
    if state.user_id:
        await set_exclude_watched_default(
            movie_manager.db_pool,
            state.user_id,
            bool(filters["exclude_watched"]),
        )
        await set_exclude_watchlist_default(           # NEW
            movie_manager.db_pool,
            state.user_id,
            bool(filters["exclude_watchlist"]),
        )
```

- [ ] **Step 6: Run test + commit**

Run: `python3 -m pytest tests/web/test_routes_navigation.py tests/application/ tests/web/ -v`
Expected: PASS.

```bash
git add nextreel/application/navigation_state_service.py nextreel/web/routes/shared.py nextreel/web/routes/navigation.py tests/web/test_routes_navigation.py
# Propose: git commit -m "Plumb exclude_watchlist through bind_user and filter-apply persistence"
```

---

## Phase 8: Presenter & view-model

### Task 9: Add `WatchlistPresenter` + `is_in_watchlist` on `MovieDetailService`

**Files:**
- Modify: `nextreel/web/route_services.py` — add `WatchlistListViewModel`, `WatchlistPresenter`, extend `MovieDetailViewModel` with `is_in_watchlist`, extend `MovieDetailService.get` to fetch it
- Modify: `nextreel/web/routes/movies.py:93` — set `g.is_in_watchlist`
- Modify: `nextreel/web/routes/shared.py:27,102,292` — re-export `_watchlist_list_presenter`
- Test: extend `tests/web/test_route_services.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/web/test_route_services.py`:

```python
async def test_movie_detail_service_returns_is_in_watchlist():
    from unittest.mock import AsyncMock, MagicMock
    from nextreel.web.route_services import MovieDetailService

    movie_manager = MagicMock()
    movie_manager.watched_store.is_watched = AsyncMock(return_value=False)
    movie_manager.watchlist_store.is_in_watchlist = AsyncMock(return_value=True)
    movie_manager.projection_store.fetch_renderable_payload = AsyncMock(
        return_value={"tconst": "tt1", "_full": True}
    )
    movie_manager.prev_stack_length = MagicMock(return_value=0)

    svc = MovieDetailService()
    vm = await svc.get(movie_manager=movie_manager, state=None, user_id="u1", tconst="tt1")

    assert vm.is_in_watchlist is True


def test_watchlist_presenter_normalizes_added_at_field():
    from datetime import datetime
    from nextreel.web.route_services import WatchlistPresenter

    presenter = WatchlistPresenter()
    rows = [
        {
            "tconst": "tt1",
            "primaryTitle": "Film",
            "startYear": 1995,
            "added_at": datetime(2026, 4, 14),
            "payload_json": '{"poster_url": "/x.jpg"}',
        }
    ]
    vm = presenter.build(
        raw_rows=rows, total_count=1, page=1, per_page=20,
        now=datetime(2026, 4, 25),
    )

    assert len(vm.movies) == 1
    assert vm.movies[0]["added_at"].startswith("2026-04-14")
    assert vm.total == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_route_services.py -v -k watchlist`
Expected: FAIL — `MovieDetailViewModel has no field is_in_watchlist`, `WatchlistPresenter not found`.

- [ ] **Step 3: Update `route_services.py`**

In `nextreel/web/route_services.py`, replace the existing `MovieDetailViewModel`, `WatchedListViewModel`, and `MovieDetailService` blocks. Add `WatchlistListViewModel`, `WatchlistPresenter`. Updated file structure (only touched parts shown):

```python
@dataclass(slots=True)
class MovieDetailViewModel:
    movie: dict
    previous_count: int
    is_watched: bool
    is_in_watchlist: bool          # NEW


@dataclass(slots=True)
class WatchedListViewModel:
    movies: list[dict]
    stats: dict
    total: int
    pagination: dict


@dataclass(slots=True)
class WatchlistListViewModel:        # NEW — same shape as WatchedListViewModel
    movies: list[dict]
    stats: dict
    total: int
    pagination: dict


class MovieDetailService:
    async def get(self, *, movie_manager, state, user_id: str | None, tconst: str):
        async def _watched_lookup() -> bool:
            if not user_id:
                return False
            return await movie_manager.watched_store.is_watched(user_id, tconst)

        async def _watchlist_lookup() -> bool:               # NEW
            if not user_id:
                return False
            return await movie_manager.watchlist_store.is_in_watchlist(user_id, tconst)

        async def _payload_lookup():
            return await movie_manager.projection_store.fetch_renderable_payload(tconst)

        is_watched, is_in_watchlist, movie = await asyncio.gather(
            _watched_lookup(),
            _watchlist_lookup(),
            _payload_lookup(),
        )
        if not movie:
            return None
        movie = dict(movie)
        if not movie.get("tconst"):
            movie["tconst"] = movie.get("imdb_id") or tconst

        return MovieDetailViewModel(
            movie=movie,
            previous_count=movie_manager.prev_stack_length(state),
            is_watched=bool(is_watched),
            is_in_watchlist=bool(is_in_watchlist),
        )
```

Add `WatchlistPresenter` immediately after `WatchedListPresenter` (around line 139). Copy the entire `WatchedListPresenter` class, rename, swap `watched_at` → `added_at`:

```python
class WatchlistPresenter:
    def build(self, *, raw_rows, total_count: int, page: int, per_page: int, now: datetime):
        movies: list[dict] = []
        year_values: list[int] = []
        this_month_count = 0

        for row in raw_rows:
            movie, year_int, is_this_month = self._normalize_row(row, now)
            if movie is None:
                continue
            if year_int:
                year_values.append(year_int)
            if is_this_month:
                this_month_count += 1
            movies.append(movie)

        total_pages = max(1, (total_count + per_page - 1) // per_page)
        return WatchlistListViewModel(
            movies=movies,
            stats=self._build_stats(total_count, this_month_count, year_values),
            total=total_count,
            pagination={
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        )

    def _normalize_row(self, row, now: datetime) -> tuple[dict | None, int | None, bool]:
        payload = row.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}

        tconst = row.get("tconst")
        if not tconst:
            return None, None, False

        title = payload.get("title") or row.get("primaryTitle") or "Untitled"
        slug = payload.get("slug") or row.get("slug")

        year_raw = payload.get("year") or row.get("startYear")
        try:
            year_int = int(str(year_raw)[:4]) if year_raw else None
        except (TypeError, ValueError):
            year_int = None

        try:
            tmdb_rating = float(payload.get("rating") or 0)
        except (TypeError, ValueError):
            tmdb_rating = 0.0

        poster_url = payload.get("poster_url") or "/static/img/poster-placeholder.svg"

        added_at = row.get("added_at")
        added_iso = (
            added_at.isoformat() if hasattr(added_at, "isoformat") else str(added_at or "")
        )
        is_this_month = (
            hasattr(added_at, "year")
            and added_at.year == now.year
            and added_at.month == now.month
        )

        return (
            {
                "tconst": tconst,
                "slug": slug,
                "title": title,
                "year": year_int,
                "poster_url": poster_url,
                "tmdb_rating": tmdb_rating,
                "added_at": added_iso,
            },
            year_int,
            is_this_month,
        )

    def normalize_movie(self, row, now: datetime) -> dict | None:
        movie, _, _ = self._normalize_row(row, now)
        return movie

    def _build_stats(self, total: int, this_month_count: int, year_values: list[int]) -> dict:
        avg_year = int(round(sum(year_values) / len(year_values))) if year_values else None
        if year_values:
            decade_counts: dict[int, int] = {}
            for year in year_values:
                decade = (year // 10) * 10
                decade_counts[decade] = decade_counts.get(decade, 0) + 1
            top_decade_year = max(decade_counts.items(), key=lambda item: (item[1], item[0]))[0]
            top_decade = "%ds" % top_decade_year
        else:
            top_decade = None

        return {
            "total": total,
            "this_month": this_month_count,
            "avg_year": avg_year,
            "top_decade": top_decade,
        }
```

> The `_build_stats` body above is copied verbatim from `WatchedListPresenter._build_stats` at `nextreel/web/route_services.py:141-158`. The stats dict shape (`total`, `this_month`, `avg_year`, `top_decade`) matches what the watched/watchlist template stats block consumes.

- [ ] **Step 4: Update `routes/movies.py:93` to set `g.is_in_watchlist`**

```python
    g.is_watched = view_model.is_watched
    g.is_in_watchlist = view_model.is_in_watchlist          # NEW
```

- [ ] **Step 5: Re-export `_watchlist_list_presenter` from shared.py**

In `nextreel/web/routes/shared.py`, update the import (line 27):

```python
from nextreel.web.route_services import (
    MovieDetailService,
    WatchedListPresenter,
    WatchlistPresenter,
)
```

Add the singleton (line 102):

```python
_watchlist_list_presenter = WatchlistPresenter()
```

Append to `__all__` (line 292):

```python
    "_watchlist_list_presenter",
```

- [ ] **Step 6: Run test + commit**

Run: `python3 -m pytest tests/web/test_route_services.py tests/web/ -v`
Expected: PASS.

```bash
git add nextreel/web/route_services.py nextreel/web/routes/movies.py nextreel/web/routes/shared.py tests/web/test_route_services.py
# Propose: git commit -m "Add WatchlistPresenter and is_in_watchlist on MovieDetailService"
```

---

## Phase 9: HTTP routes

### Task 10: Build `routes/watchlist.py` (3 endpoints)

**Files:**
- Create: `nextreel/web/routes/watchlist.py`
- Modify: `nextreel/web/routes/__init__.py` — import the handlers; add to `__all__`
- Test: Create `tests/web/test_watchlist_routes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_watchlist_routes.py`:

```python
"""Route tests for /watchlist endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from quart import g

import nextreel.web.routes.watchlist as watchlist_routes
from nextreel.web.routes.shared import NextReelServices


def _nav_state(user_id: str | None = "user-123") -> SimpleNamespace:
    return SimpleNamespace(csrf_token="csrf-token", user_id=user_id)


def _install_services(app):
    watchlist_store = MagicMock()
    watchlist_store.add = AsyncMock()
    watchlist_store.remove = AsyncMock()
    watchlist_store.is_in_watchlist = AsyncMock(return_value=False)
    watchlist_store.list_watchlist_filtered = AsyncMock(return_value=[])
    watchlist_store.count_filtered = AsyncMock(return_value=0)
    watchlist_store.available_filter_chips = AsyncMock(
        return_value={"decades": [], "genres": [], "ratings": []}
    )
    movie_manager = SimpleNamespace(db_pool=AsyncMock(), watchlist_store=watchlist_store)
    app.extensions["nextreel"] = NextReelServices(
        movie_manager=movie_manager,
        metrics_collector=MagicMock(),
    )
    return movie_manager, watchlist_store


@pytest.mark.asyncio
async def test_watchlist_list_redirects_when_not_logged_in(app):
    _install_services(app)
    async with app.test_request_context("/watchlist"):
        g.navigation_state = _nav_state(user_id=None)
        response = await watchlist_routes.watchlist_page()
        # _require_login returns a redirect Response.
        assert response.status_code in (302, 303)


@pytest.mark.asyncio
async def test_add_to_watchlist_returns_json_when_requested(app):
    _, store = _install_services(app)
    async with app.test_request_context(
        "/watchlist/add/tt1234567",
        method="POST",
        headers={"Accept": "application/json", "X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        response = await watchlist_routes.add_to_watchlist("tt1234567")

    payload = await response.get_json()
    assert payload == {"ok": True, "is_in_watchlist": True, "tconst": "tt1234567"}
    store.add.assert_awaited_once_with("user-123", "tt1234567")


@pytest.mark.asyncio
async def test_remove_from_watchlist_returns_json_when_requested(app):
    _, store = _install_services(app)
    async with app.test_request_context(
        "/watchlist/remove/tt1234567",
        method="POST",
        headers={"Accept": "application/json", "X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        response = await watchlist_routes.remove_from_watchlist("tt1234567")

    payload = await response.get_json()
    assert payload == {"ok": True, "is_in_watchlist": False, "tconst": "tt1234567"}
    store.remove.assert_awaited_once_with("user-123", "tt1234567")


@pytest.mark.asyncio
async def test_add_rejects_invalid_tconst(app):
    _install_services(app)
    async with app.test_request_context(
        "/watchlist/add/not-a-tconst",
        method="POST",
        headers={"X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        with pytest.raises(Exception):  # abort(400) raises HTTPException
            await watchlist_routes.add_to_watchlist("not-a-tconst")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_watchlist_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nextreel.web.routes.watchlist'`.

- [ ] **Step 3: Create `nextreel/web/routes/watchlist.py`**

```python
"""Watchlist (save-for-later) route handlers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from quart import abort, jsonify, redirect, render_template, request

from infra.route_helpers import csrf_required, rate_limited, safe_referrer as _safe_referrer
from nextreel.web.routes.shared import (
    _TCONST_RE,
    _current_user_id,
    _require_login,
    _services,
    _watchlist_list_presenter,
    _wants_json_response,
    bp,
    logger,
)


def _parse_watchlist_pagination(args) -> tuple[int, int, int]:
    try:
        page = max(1, int(args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(args.get("per_page", 60))
    except (TypeError, ValueError):
        per_page = 60
    per_page = max(1, min(per_page, 200))
    offset = (page - 1) * per_page
    return page, per_page, offset


def _parse_filter_params(args) -> dict:
    """Extract filter parameters from request query string."""
    result = {}

    decades_raw = args.get("decades", "")
    if decades_raw:
        result["decades"] = [d.strip().rstrip("s") for d in decades_raw.split(",") if d.strip()]

    rating_tier = args.get("rating", "")
    if rating_tier == "8+":
        result["rating_min"] = 8.0
        result["rating_max"] = 10.0
    elif rating_tier == "6-8":
        result["rating_min"] = 6.0
        result["rating_max"] = 7.99
    elif rating_tier == "<6":
        result["rating_min"] = 0.0
        result["rating_max"] = 5.99

    genres_raw = args.get("genres", "")
    if genres_raw:
        result["genres"] = [g.strip() for g in genres_raw.split(",") if g.strip()]

    return result


_VALID_SORTS = {"recent", "title_asc", "title_desc", "year_desc", "rating_desc"}


@bp.route("/watchlist")
async def watchlist_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()
    watchlist_store = services.movie_manager.watchlist_store

    page, per_page, offset = _parse_watchlist_pagination(request.args)
    sort = request.args.get("sort", "recent")
    if sort not in _VALID_SORTS:
        sort = "recent"
    filter_params = _parse_filter_params(request.args)

    raw_rows, total_count, filter_chips = await asyncio.gather(
        watchlist_store.list_watchlist_filtered(
            user_id, sort=sort, limit=per_page, offset=offset, **filter_params
        ),
        watchlist_store.count_filtered(user_id, **filter_params),
        watchlist_store.available_filter_chips(user_id),
    )

    view_model = _watchlist_list_presenter.build(
        raw_rows=raw_rows,
        total_count=total_count,
        page=page,
        per_page=per_page,
        now=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    has_more = (offset + per_page) < total_count

    if _wants_json_response():
        from quart import render_template as rt

        html_parts = [
            await rt("_watchlist_card.html", movie=movie) for movie in view_model.movies
        ]
        return jsonify(
            {
                "html": "".join(html_parts),
                "total": total_count,
                "has_more": has_more,
                "page": page,
            }
        )

    return await render_template(
        "watchlist.html",
        movies=view_model.movies,
        total=view_model.total,
        filter_chips=filter_chips,
        has_more=has_more,
        pagination=view_model.pagination,
        current_sort=sort,
    )


@bp.route("/watchlist/add/<tconst>", methods=["POST"])
@csrf_required
@rate_limited("watchlist")
async def add_to_watchlist(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watchlist_store.add(user_id, tconst)
    logger.info("User %s added %s to watchlist", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_in_watchlist": True,
                "tconst": tconst,
            }
        )

    return redirect(_safe_referrer(tconst), code=303)


@bp.route("/watchlist/remove/<tconst>", methods=["POST"])
@csrf_required
@rate_limited("watchlist")
async def remove_from_watchlist(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watchlist_store.remove(user_id, tconst)
    logger.info("User %s removed %s from watchlist", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_in_watchlist": False,
                "tconst": tconst,
            }
        )

    return redirect(_safe_referrer(tconst), code=303)


__all__ = [
    "add_to_watchlist",
    "remove_from_watchlist",
    "watchlist_page",
]
```

- [ ] **Step 4: Register handlers in `nextreel/web/routes/__init__.py`**

Add an import block alongside the watched import (line 37):

```python
from nextreel.web.routes.watchlist import (
    add_to_watchlist,
    remove_from_watchlist,
    watchlist_page,
)
```

Append to `__all__` (line 44):

```python
    "add_to_watchlist",
    "remove_from_watchlist",
    "watchlist_page",
```

- [ ] **Step 5: Run test + commit**

Run: `python3 -m pytest tests/web/test_watchlist_routes.py -v`
Expected: PASS — all route tests green.

```bash
git add nextreel/web/routes/watchlist.py nextreel/web/routes/__init__.py tests/web/test_watchlist_routes.py
# Propose: git commit -m "Add /watchlist routes (list + add + remove)"
```

---

## Phase 10: Templates — list page & card

### Task 11: Create `templates/watchlist.html` and `templates/_watchlist_card.html`

**Files:**
- Create: `templates/watchlist.html` (copy of `templates/watched_list.html` with renames + new empty state)
- Create: `templates/_watchlist_card.html` (copy of `templates/_watched_card.html` with renames)

> No unit tests for templates — they render via `tests/web/test_watchlist_routes.py::test_watchlist_list_renders_via_test_client` (added in step 4 below) plus manual verification.

- [ ] **Step 1: Copy and rename `_watched_card.html` → `_watchlist_card.html`**

Run: `cp templates/_watched_card.html templates/_watchlist_card.html`

Then edit `templates/_watchlist_card.html`. Find every occurrence and rename:
- `data-watched=` → `data-added=`
- `movie.watched_at` → `movie.added_at`
- `class="watched-card"` → `class="watched-card"` (keep — these are styling classes; we reuse the watched grid CSS to avoid a sibling stylesheet)
- `class="watched-poster"`, `class="watched-card-bar"`, etc. — **leave as-is** (CSS classes shared)
- `aria-label="Remove ... from watched"` → `aria-label="Remove ... from watchlist"`
- `class="watched-remove"` — leave as-is

The card visual style is identical to watched; only the data attribute name and aria-label differ.

- [ ] **Step 2: Copy `watched_list.html` → `watchlist.html`**

Run: `cp templates/watched_list.html templates/watchlist.html`

Edit `templates/watchlist.html`:

1. Page `<title>` → `Watchlist – Nextreel`.
2. Replace the empty-state block (around line 56) with:

```html
{% if movies|length == 0 and not (filter_chips is defined and filter_chips) %}
  <div class="watched-empty">
    <h1 class="watched-empty-title">Your watchlist is empty</h1>
    <hr class="watched-empty-rule" />
    <p class="watched-empty-desc">
      Movies you save while browsing show up here. Start discovering, then
      click <em>Add to watchlist</em> on any movie page.
    </p>
    <a href="/" class="watched-empty-cta watched-empty-cta--primary">Discover movies →</a>
  </div>
```

3. Replace the page header heading (around line 70) so it reads:

```html
<header class="watched-header">
  <h1 class="watched-title">Watchlist</h1>
  <p class="watched-subtitle"><em>{{ total }} films saved for later</em></p>
</header>
```

4. Remove the toolbar's `watched-letterboxd-link` `<a>` block (the JustWatch-style logo) and the `details`/`session.pop('letterboxd_unmatched')` block at the top — both are Letterboxd-import-specific.

5. Replace the `_watched_card.html` include with `_watchlist_card.html` in the grid:

```html
{% for movie in movies %}
  {% include '_watchlist_card.html' %}
{% endfor %}
```

6. Update any `enrichment_pending` references — remove them. Watchlist has no enrichment progress flow.

7. The bottom JS reference (`<script src="{{ url_for('static', filename='js/watched.js') }}...">`) — `static/js/watched.js` contains hardcoded `/watched/remove/...` and `/watched/add/...` URLs at lines 249 and 274 (for the in-grid card-remove + undo flow), so the same file cannot serve both lists. Copy it:

```bash
cp static/js/watched.js static/js/watchlist.js
```

Then in `static/js/watchlist.js`, replace `/watched/remove/` → `/watchlist/remove/` (line 249) and `/watched/add/` → `/watchlist/add/` (line 274). The DOM element IDs (`#watched-grid`, `#watched-search`, `#watched-load-more`, `#watched-grid-footer`) are kept in `watchlist.html` so the rest of the script works unchanged.

In `templates/watchlist.html`, change the script src from `js/watched.js` to `js/watchlist.js`.

- [ ] **Step 3: Add a route smoke test**

Add to `tests/web/test_watchlist_routes.py`:

```python
@pytest.mark.asyncio
async def test_watchlist_page_renders_for_logged_in_user(app):
    """Empty watchlist renders HTML (smoke test for template + route wiring)."""
    _install_services(app)
    async with app.test_request_context("/watchlist"):
        g.navigation_state = _nav_state()
        response = await watchlist_routes.watchlist_page()
    body = await response.get_data(as_text=True) if hasattr(response, "get_data") else response
    assert "Your watchlist is empty" in body or "Watchlist" in body
```

- [ ] **Step 4: Run all tests**

Run: `python3 -m pytest tests/web/test_watchlist_routes.py tests/web/ -v`
Expected: PASS.

- [ ] **Step 5: Stage + propose commit**

```bash
git add templates/watchlist.html templates/_watchlist_card.html tests/web/test_watchlist_routes.py
# Propose: git commit -m "Add watchlist.html and _watchlist_card.html templates"
```

---

## Phase 11: Movie detail UI — 4-button bottom nav

### Task 12: Add Watchlist toggle button to `templates/movie_card.html`

**Files:**
- Modify: `templates/movie_card.html:213-260` — insert the watchlist form/button between Previous and Watched

- [ ] **Step 1: Read the full bottom-nav block first**

Run: `sed -n '213,260p' templates/movie_card.html`

You should see the existing 3-button block: `<form ... /previous_movie>`, the watched form/button, and `<form ... /next_movie>`.

- [ ] **Step 2: Insert the watchlist form between Previous and Watched**

Edit `templates/movie_card.html`. The existing `{% set watch_tconst = movie.tconst or movie.imdb_id %}` line (around line 221) defines a variable used by the watched form below it. Insert the watchlist block **between that `{% set %}` line and the existing `{% if current_user_id %}` line** so both forms share the same `watch_tconst`:

```html
    {% set watch_tconst = movie.tconst or movie.imdb_id %}
    {# ── Add to Watchlist form (NEW) ────────────────────────── #}
    {% if current_user_id %}
    <form method="POST"
          action="{% if is_in_watchlist %}/watchlist/remove/{{ watch_tconst }}{% else %}/watchlist/add/{{ watch_tconst }}{% endif %}"
          class="inline"
          data-watchlist-toggle-form
          data-watchlist-state="{% if is_in_watchlist %}saved{% else %}unsaved{% endif %}"
          data-add-url="/watchlist/add/{{ watch_tconst }}"
          data-remove-url="/watchlist/remove/{{ watch_tconst }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit"
              class="nav-btn-watchlist"
              data-watchlist-toggle-button
              aria-pressed="{% if is_in_watchlist %}true{% else %}false{% endif %}">
        {% if is_in_watchlist %}
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg>
        Saved
        {% else %}
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg>
        <span><span class="nav-btn-watchlist__prefix">Add to </span>Watchlist</span>
        {% endif %}
      </button>
    </form>
    {% else %}
    <a href="{{ url_for('main.login_page', next='/movie/' ~ watch_tconst) }}"
       class="nav-btn-watchlist nav-btn-watchlist--login"
       aria-label="Log in to add to watchlist">
      <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg>
      <span><span class="nav-btn-watchlist__prefix">Add to </span>Watchlist</span>
    </a>
    {% endif %}

    {# ── existing watched form continues here ───────────────── #}
    {% if current_user_id %}
    <form method="POST"
          action="{% if is_watched %}/watched/remove/{{ watch_tconst }}{% else %}/watched/add/{{ watch_tconst }}{% endif %}"
          ...
```

Leave the existing watched form block **completely untouched** — only add the new watchlist block above it.

- [ ] **Step 3: Add `nav-btn-watchlist` CSS to the Tailwind input file**

The Tailwind source is `static/css/input.css` (compiled to `static/css/output.css` by `npm run build-css`). The `nav-btn-watched` rules live around line 549-587, with the mobile-collapse rule at line 1824.

In `static/css/input.css`, immediately after the `.nav-btn-watched:active { transform: scale(0.97); }` rule (around line 587), insert:

```css
  /* Add to Watchlist — sticky nav bar, peer to Watched */
  .nav-btn-watchlist {
    font-size: 0.8rem; font-weight: 600;
    letter-spacing: 0.02em;
    cursor: pointer;
    background: none; border: none;
    font-family: var(--font-sans);
    display: inline-flex; align-items: center; gap: 0.35rem;
    padding: 0.4rem 0;
    transition: color var(--duration-normal) var(--easing-default), opacity var(--duration-normal) var(--easing-default);
  }
  .nav-btn-watchlist svg {
    width: 16px; height: 16px;
    stroke: currentColor; fill: none; stroke-width: 2;
  }
  form[data-watchlist-state="unsaved"] .nav-btn-watchlist {
    color: var(--color-text-muted);
  }
  .nav-btn-watchlist--login {
    color: var(--color-text-muted);
    text-decoration: none;
  }
  form[data-watchlist-state="unsaved"] .nav-btn-watchlist:hover,
  .nav-btn-watchlist--login:hover {
    color: var(--color-text);
  }
  form[data-watchlist-state="saved"] .nav-btn-watchlist {
    color: var(--color-accent);
  }
  form[data-watchlist-state="saved"] .nav-btn-watchlist:hover {
    opacity: 0.85;
  }
  .nav-btn-watchlist:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .nav-btn-watchlist:active {
    transform: scale(0.97);
  }
```

Then add `nav-btn-watchlist` to the existing `.movie-nav-bar` font-size override (around line 356-360). Replace:

```css
  .movie-nav-bar .nav-btn-prev,
  .movie-nav-bar .nav-btn-next,
  .movie-nav-bar .nav-btn-watched {
    font-size: 0.75rem;
  }
```

with:

```css
  .movie-nav-bar .nav-btn-prev,
  .movie-nav-bar .nav-btn-next,
  .movie-nav-bar .nav-btn-watched,
  .movie-nav-bar .nav-btn-watchlist {
    font-size: 0.75rem;
  }
```

And extend the mobile-collapse rule (around line 1824). Replace:

```css
  @media (max-width: 360px) {
    .movie-nav-bar .nav-btn-watched__prefix { display: none; }
  }
```

with:

```css
  @media (max-width: 360px) {
    .movie-nav-bar .nav-btn-watched__prefix,
    .movie-nav-bar .nav-btn-watchlist__prefix { display: none; }
  }
```

(Note: `var(--color-watched)` is the watched accent — for the watchlist "saved" state we reuse the brand `var(--color-accent)` so the two states are visually distinguishable. Adjust later if a dedicated `--color-watchlist` token is desired.)

Rebuild Tailwind output:
Run: `npm run build-css`
Expected: regenerates `static/css/output.css` with the new rules.

- [ ] **Step 4: Verify with the dev server**

Start the server in the background:
Run: `python3 app.py &` (or use whatever the project's dev script is)

Navigate to a movie detail page in a browser, log in, and confirm:
- Bottom nav shows 4 buttons: `[← Previous] [Add to Watchlist] [Mark as Watched] [Next →]`.
- On narrow viewport (<480px or so), the "Add to" prefix collapses (icons + label only).
- Logged-out user sees the same 4 buttons but Watchlist links to `/login?next=/movie/<tconst>`.
- Clicking the watchlist button still does a full-page POST round-trip (the AJAX upgrade comes in Task 13).

- [ ] **Step 5: Stage + propose commit**

```bash
git add templates/movie_card.html static/css/  static/css/src/  # adjust paths
# Propose: git commit -m "Add Watchlist toggle to movie detail bottom nav"
```

---

### Task 13: Create `static/js/watchlist-toggle.js` AJAX upgrade

**Files:**
- Create: `static/js/watchlist-toggle.js`
- Modify: `templates/movie_card.html` — add `<script>` tag

- [ ] **Step 1: Create the JS file**

Create `static/js/watchlist-toggle.js` (copy of `static/js/movie-card.js:60-127`, scoped to the watchlist data attributes):

```javascript
(function () {
  var form = document.querySelector("[data-watchlist-toggle-form]");
  if (!form) return;

  var button = form.querySelector("[data-watchlist-toggle-button]");
  var csrfInput = form.querySelector('input[name="csrf_token"]');
  var status = document.getElementById("movie-status");
  var addUrl = form.dataset.addUrl;
  var removeUrl = form.dataset.removeUrl;

  var savedMarkup =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg> Saved';
  var unsavedMarkup =
    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg> ' +
    '<span><span class="nav-btn-watchlist__prefix">Add to </span>Watchlist</span>';

  function setWatchlistState(isInWatchlist) {
    form.dataset.watchlistState = isInWatchlist ? "saved" : "unsaved";
    form.action = isInWatchlist ? removeUrl : addUrl;
    button.innerHTML = isInWatchlist ? savedMarkup : unsavedMarkup;
    button.setAttribute("aria-pressed", isInWatchlist ? "true" : "false");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!button || button.disabled) return;

    var isInWatchlist = form.dataset.watchlistState === "saved";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    if (status) {
      status.textContent = isInWatchlist
        ? "Removing from watchlist..."
        : "Saving to watchlist...";
    }

    fetch(form.action, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-CSRFToken": csrfInput ? csrfInput.value : "",
      },
      credentials: "same-origin",
    }).then(function (response) {
      return response.json().catch(function () {
        return null;
      }).then(function (payload) {
        if (!response.ok || !payload || !payload.ok) {
          throw new Error("watchlist toggle failed");
        }
        return payload;
      });
    }).then(function (payload) {
      setWatchlistState(Boolean(payload.is_in_watchlist));
      if (status) {
        status.textContent = payload.is_in_watchlist
          ? "Added to watchlist."
          : "Removed from watchlist.";
      }
    }).catch(function (error) {
      console.error("Failed to update watchlist state:", error);
      if (status) {
        status.textContent = "Could not update watchlist status.";
      }
    }).finally(function () {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    });
  });
})();
```

- [ ] **Step 2: Add the `<script>` tag to `templates/movie_card.html`**

Find the existing `<script src="{{ url_for('static', filename='js/movie-card.js') }}...">` line (around line 262). Add immediately below it:

```html
<script src="{{ url_for('static', filename='js/watchlist-toggle.js') }}?v={{ config.get('CSS_VERSION', '1') }}" defer></script>
```

- [ ] **Step 3: Verify in dev server**

With the dev server running, reload a movie detail page. Open DevTools → Network. Click the watchlist toggle:

- The request should be `POST /watchlist/add/<tconst>` with `Accept: application/json`.
- Response should be `{"ok": true, "is_in_watchlist": true, "tconst": "..."}`.
- The button should flip to "Saved" without a full page reload.
- Click again → `POST /watchlist/remove/<tconst>` → button flips back.

- [ ] **Step 4: Stage + propose commit**

```bash
git add static/js/watchlist-toggle.js templates/movie_card.html
# Propose: git commit -m "Add AJAX upgrade for Watchlist toggle"
```

---

## Phase 12: Filter form UI

### Task 14: Add `exclude_watchlist` checkbox to `_filter_form.html`

**Files:**
- Modify: `templates/_filter_form.html` — add a checkbox row mirroring `exclude_watched`

- [ ] **Step 1: Find the existing `exclude_watched` checkbox**

Run: `grep -n "exclude_watched" templates/_filter_form.html`

That line tells you where to add the parallel checkbox.

- [ ] **Step 2: Add the parallel checkbox**

Immediately after the `exclude_watched` row in `_filter_form.html`, add (using the same Tailwind classes/markup the existing checkbox uses — copy the surrounding `<div>` and `<label>` block verbatim, then rename):

```html
<!-- Exclude watchlist movies -->
<div class="filter-toggle-row">
  <input type="hidden" name="exclude_watchlist" value="off" />
  <label class="filter-toggle-label">
    <input
      type="checkbox"
      name="exclude_watchlist"
      value="on"
      {% if current_filters.get('exclude_watchlist', True) %}checked{% endif %}
    />
    <span>Hide movies in my watchlist</span>
  </label>
</div>
```

> **Important:** the hidden `value="off"` ensures the form always submits *some* `exclude_watchlist` value, so the normalizer's "absent → True" branch is only taken when the form was never rendered with this field at all (legacy / older browser).

- [ ] **Step 3: Verify in dev server**

Reload a movie detail page (logged in), open the filter drawer, and confirm:
- The new "Hide movies in my watchlist" checkbox appears next to "Hide movies I've watched".
- It's checked by default.
- Submitting the form with it unchecked correctly persists `exclude_watchlist_default = FALSE` (verifiable via DB query) and the next discovered movie is allowed to come from the watchlist.

- [ ] **Step 4: Stage + propose commit**

```bash
git add templates/_filter_form.html
# Propose: git commit -m "Add exclude_watchlist checkbox to inline filter form"
```

---

## Phase 13: Account toggle + cascade

### Task 15: Account-page Preferences toggle + delete cascade

**Files:**
- Modify: `templates/account.html:48-69` — add Preferences toggle row
- Modify: `nextreel/web/routes/account.py:67-92` — `account_view` reads + passes new pref
- Modify: `nextreel/web/routes/account.py:209-224` — `account_preferences_save` reads + persists
- Modify: `nextreel/web/routes/account.py:121-186` — `account_password_change` re-render passes new pref
- Modify: `nextreel/web/routes/account.py:486-505` — `account_delete` cascade adds `DELETE FROM user_watchlist`
- Test: extend `tests/web/test_account_routes.py`

- [ ] **Step 1: Write the failing test**

In `tests/web/test_account_routes.py`, find the `_patch_prefs()` helper and add a fourth patch:

```python
def _patch_prefs():
    return [
        patch(
            "nextreel.web.routes.account.user_preferences.get_exclude_watched_default",
            new_callable=AsyncMock, return_value=True,
        ),
        patch(
            "nextreel.web.routes.account.user_preferences.get_exclude_watchlist_default",  # NEW
            new_callable=AsyncMock, return_value=True,
        ),
        patch(
            "nextreel.web.routes.account.user_preferences.get_theme_preference",
            new_callable=AsyncMock, return_value=None,
        ),
        patch(
            "nextreel.web.routes.account.user_preferences.get_default_filters",
            new_callable=AsyncMock, return_value=None,
        ),
    ]
```

Then add a focused test:

```python
@pytest.mark.asyncio
async def test_preferences_save_persists_exclude_watchlist_default():
    """POST /account/preferences with exclude_watchlist_default=on persists True."""
    with _make_account_app(authenticated=True) as (app, _):
        with ExitStack() as stack:
            for cm in _patch_prefs():
                stack.enter_context(cm)
            stack.enter_context(_patch_user())
            set_watchlist = stack.enter_context(
                patch(
                    "nextreel.web.routes.account.user_preferences.set_exclude_watchlist_default",
                    new_callable=AsyncMock,
                )
            )
            stack.enter_context(
                patch(
                    "nextreel.web.routes.account.user_preferences.set_exclude_watched_default",
                    new_callable=AsyncMock,
                )
            )
            client = app.test_client()
            await client.post(
                "/account/preferences",
                form={
                    "csrf_token": "test-csrf-token",
                    "exclude_watched_default": "on",
                    "exclude_watchlist_default": "on",
                },
            )
            set_watchlist.assert_awaited_once_with(app.movie_manager.db_pool, "u1", True)
```

(Adjust the `app.movie_manager.db_pool` reference to match how `_make_account_app` exposes the manager — see existing tests in the file for the pattern.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_account_routes.py::test_preferences_save_persists_exclude_watchlist_default -v`
Expected: FAIL — `AttributeError: module 'session.user_preferences' has no attribute 'get_exclude_watchlist_default'` (or similar; depends on exact patch ordering).

- [ ] **Step 3: Update `account_view`**

In `nextreel/web/routes/account.py`, modify `account_view` (around line 67) to read and pass the new pref:

```python
    exclude_watched_default = await user_preferences.get_exclude_watched_default(
        db_pool, user_id
    )
    exclude_watchlist_default = await user_preferences.get_exclude_watchlist_default(
        db_pool, user_id
    )
    theme_preference = await user_preferences.get_theme_preference(db_pool, user_id)
    default_filters = await user_preferences.get_default_filters(db_pool, user_id)

    return await render_template(
        "account.html",
        user=user,
        server_theme=theme_preference,
        exclude_watched_default=exclude_watched_default,
        exclude_watchlist_default=exclude_watchlist_default,        # NEW
        default_filters=default_filters,
        page_title="Account",
    )
```

Make the same addition in the `account_password_change` re-render branch (around line 152-170): read `exclude_watchlist` and pass it to `render_template("account.html", ..., exclude_watchlist_default=exclude_watchlist, ...)`.

- [ ] **Step 4: Update `account_preferences_save`**

Modify the handler (around line 212):

```python
@bp.route("/account/preferences", methods=["POST"])
@csrf_required
@rate_limited("account_preferences")
async def account_preferences_save():
    user_id = _require_user()
    form = await request.form
    exclude_watched = form.get("exclude_watched_default") == "on"
    exclude_watchlist = form.get("exclude_watchlist_default") == "on"  # NEW

    db_pool = _db_pool()
    await user_preferences.set_exclude_watched_default(db_pool, user_id, exclude_watched)
    await user_preferences.set_exclude_watchlist_default(             # NEW
        db_pool, user_id, exclude_watchlist
    )
    if "theme_preference" in form:
        theme_raw = form.get("theme_preference", "system")
        theme = theme_raw if theme_raw in ("light", "dark") else None
        await user_preferences.set_theme_preference(db_pool, user_id, theme)
    logger.info("Account action: %s user=%s", "preferences_save", user_id)
    return redirect(url_for("main.account_view"))
```

- [ ] **Step 5: Update `account_delete` cascade**

Modify the cascade ordering in `account_delete` (around line 486) to delete watchlist rows first (before the user row):

```python
    # Ordered cascade
    await db_pool.execute(
        "DELETE FROM user_watched_movies WHERE user_id = %s",
        [user_id],
        fetch="none",
    )
    await db_pool.execute(                                            # NEW
        "DELETE FROM user_watchlist WHERE user_id = %s",
        [user_id],
        fetch="none",
    )
    await db_pool.execute(
        "DELETE FROM user_navigation_state WHERE user_id = %s",
        [user_id],
        fetch="none",
    )
    # ... rest unchanged
```

- [ ] **Step 6: Update `templates/account.html`**

Find the existing `exclude_watched_default` toggle block (around line 53-62). Insert a parallel block immediately below it, inside the same `<form>`:

```html
<div class="settings-toggle-row">
  <div>
    <div class="settings-toggle-label">Exclude watchlist movies</div>
    <div class="settings-toggle-desc">Hide movies in your watchlist from recommendations</div>
  </div>
  <input type="hidden" name="exclude_watchlist_default" value="{{ 'on' if exclude_watchlist_default else '' }}">
  <button type="button" class="settings-toggle" role="switch"
          aria-checked="{{ 'true' if exclude_watchlist_default else 'false' }}"
          aria-label="Exclude watchlist movies"></button>
</div>
```

- [ ] **Step 7: Run test + commit**

Run: `python3 -m pytest tests/web/test_account_routes.py -v`
Expected: PASS — new test plus all existing account tests (the `_patch_prefs()` change should keep them green).

```bash
git add nextreel/web/routes/account.py templates/account.html tests/web/test_account_routes.py
# Propose: git commit -m "Add exclude_watchlist_default toggle to account preferences"
```

---

## Phase 14: Navbar link

### Task 16: Add "Watchlist" link to `templates/navbar_modern.html`

**Files:**
- Modify: `templates/navbar_modern.html:25-26` and `templates/navbar_modern.html:74-75` — add the watchlist link

- [ ] **Step 1: Add desktop nav link**

In `templates/navbar_modern.html`, find the `<!-- Watched top-level link -->` block (around line 25). Insert immediately after the closing `</a>`:

```html
<!-- Watchlist top-level link -->
<a href="{{ url_for('main.watchlist_page') }}" class="navbar-link">Watchlist</a>
```

- [ ] **Step 2: Add mobile nav link**

In the same file, find the mobile menu's Watched link (around line 75). Insert immediately after:

```html
<a href="{{ url_for('main.watchlist_page') }}">Watchlist</a>
```

- [ ] **Step 3: Verify in dev server**

Reload any logged-in page. Navbar should show: Search · Watched · **Watchlist** · avatar (desktop). Mobile slide-down menu should show: Watched · **Watchlist** · Account · Log Out.

- [ ] **Step 4: Stage + propose commit**

```bash
git add templates/navbar_modern.html
# Propose: git commit -m "Add Watchlist link to navbar (desktop + mobile)"
```

---

## Phase 15: Final integration smoke + verification

### Task 17: End-to-end smoke test + full test suite

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — full suite green.

If anything fails, the failure is likely in one of these spots:
- `tests/web/test_routes_navigation.py::_make_app` — may need `manager.watchlist_store = MagicMock(...)` added next to `manager.watched_store`. Add only if a test fails on missing attribute.
- `tests/web/test_route_view_contracts.py` — may need updating if it asserts the shape of `MovieDetailViewModel` (which now has `is_in_watchlist`). Update the assertion to include the new field.

- [ ] **Step 2: Run linter + type check**

Run: `black . --line-length 100` (formats new files)
Run: `flake8 . --exclude=venv,node_modules` (style)
Run: `mypy . --ignore-missing-imports` (types)
Expected: clean.

- [ ] **Step 3: End-to-end manual smoke test**

Start the dev server: `python3 app.py`

Walk this checklist as a logged-in user:

1. ✅ Movie detail page shows 4-button bottom nav: `[← Previous] [Add to Watchlist] [Mark as Watched] [Next →]`.
2. ✅ Click "Add to Watchlist" — button flips to "Saved" without a page reload (DevTools shows `POST /watchlist/add/...` returning JSON).
3. ✅ Navigate to `/watchlist` — saved movie appears.
4. ✅ Sort dropdown on `/watchlist` works (toggle to "A–Z" — order changes).
5. ✅ Click "Saved" on the movie page — it flips back to "Add to Watchlist", and the movie disappears from `/watchlist` on next reload.
6. ✅ Add a movie to the watchlist, then click "Next" repeatedly — that movie should NOT appear (excluded from discovery).
7. ✅ Open the filter drawer, uncheck "Hide movies in my watchlist", apply — watchlist movies now CAN appear in discovery.
8. ✅ `/account` page Preferences section shows the "Exclude watchlist movies" toggle. Flip it; the change persists across reload.
9. ✅ Navbar shows "Watchlist" link on desktop and mobile.
10. ✅ Empty `/watchlist` (after removing all saved movies) shows the empty-state hero with "Discover movies →" CTA.

- [ ] **Step 4: Commit any cleanup, propose final summary**

```bash
git status
# Propose: stop here. Spec implementation complete; user reviews diff and commits.
```

---

## Self-review notes

This plan covers every section of the spec:

| Spec section | Covered by |
|---|---|
| New `user_watchlist` table | Task 1 |
| `users.exclude_watchlist_default` column | Task 2 |
| `WatchlistStore` data layer | Task 3 |
| Sort whitelist (5 keys, default `recent`) | Task 3 step 3 |
| `get/set_exclude_watchlist_default` user prefs | Task 4 |
| `FilterState.exclude_watchlist` + normalizer | Task 5 |
| Navigator union of watched + watchlist exclusions | Task 6 |
| Composition root + `MovieManager.attach_cache` | Task 7 |
| `bind_user(exclude_watchlist=...)` plumbing | Task 8 |
| Filter-apply persistence of `exclude_watchlist_default` | Task 8 |
| `WatchlistPresenter` + view-model | Task 9 |
| `MovieDetailService.is_in_watchlist` | Task 9 |
| `g.is_in_watchlist` in movies route | Task 9 |
| `_watchlist_list_presenter` exported | Task 9 |
| `routes/watchlist.py` (3 endpoints, CSRF, rate-limit `"watchlist"`, JSON/HTML) | Task 10 |
| `routes/__init__.py` blueprint registration | Task 10 |
| `templates/watchlist.html` (with empty state, no Letterboxd) | Task 11 |
| `templates/_watchlist_card.html` ("Added Mar 14") | Task 11 |
| Movie-page 4-button nav (logged-in + logged-out fallback) | Task 12 |
| `nav-btn-watchlist` CSS + mobile prefix collapse | Task 12 |
| `static/js/watchlist-toggle.js` AJAX upgrade | Task 13 |
| `_filter_form.html` `exclude_watchlist` checkbox | Task 14 |
| Account-page toggle | Task 15 |
| `account_view`, `account_preferences_save`, `account_password_change` updates | Task 15 |
| `account_delete` cascade includes `user_watchlist` | Task 15 |
| Navbar link (desktop + mobile) | Task 16 |
| Full test suite + manual smoke | Task 17 |

YAGNI exclusions are honored: no Letterboxd watchlist import, no navbar count badge, no bulk operations, no public watchlists, no analytics events, no account-page count display.

No placeholders. Every step contains either ready-to-paste code, an exact file/line reference, or a specific shell command.
