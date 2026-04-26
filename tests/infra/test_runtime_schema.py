"""Tests for runtime schema verification and repair helpers."""

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pymysql
import pytest

from infra.runtime_schema import (
    _RUNTIME_SCHEMA_STATEMENTS,
    _ensure_column,
    _ensure_index,
    ensure_movie_candidates_bucket_filter_index,
    ensure_movie_candidates_fulltext_index,
    ensure_movie_candidates_refreshed_at_index,
    ensure_movie_candidates_shuffle_key,
    ensure_runtime_schema,
    ensure_user_navigation_current_ref_column,
    ensure_user_navigation_user_id_column,
)

_RUNTIME_SCHEMA_TABLES = [
    "runtime_metadata",
    "user_navigation_state",
    "movie_projection",
    "movie_candidates",
    "users",
    "user_watched_movies",
    "letterboxd_imports",
    "user_watchlist",
]

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
    "ensure_users_exclude_watchlist_default_column",
]


@contextmanager
def patched_runtime_schema_repairs():
    with ExitStack() as stack:
        mocks = {
            helper: stack.enter_context(patch(f"infra.runtime_schema.{helper}", AsyncMock()))
            for helper in _RUNTIME_SCHEMA_REPAIR_HELPERS
        }
        yield mocks


async def test_ensure_movie_candidates_fulltext_index_skips_when_present(mock_db_pool):
    """Duplicate-key errno 1061 from ALTER is swallowed as 'already exists'."""
    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(
            1061, "Duplicate key name 'ftx_movie_candidates_genres'"
        )
    )

    await ensure_movie_candidates_fulltext_index(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    alter_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert (
        "ALTER TABLE movie_candidates ADD FULLTEXT KEY ftx_movie_candidates_genres (genres)"
        in alter_query
    )


async def test_ensure_movie_candidates_fulltext_index_creates_when_missing(mock_db_pool):
    await ensure_movie_candidates_fulltext_index(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    alter_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert (
        "ALTER TABLE movie_candidates ADD FULLTEXT KEY ftx_movie_candidates_genres (genres)"
        in alter_query
    )


# ---------------------------------------------------------------------------
# _ensure_index / _ensure_column helper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_index_runs_create_sql(mock_db_pool):
    await _ensure_index(
        mock_db_pool,
        "movie_candidates",
        "idx_test",
        "CREATE INDEX idx_test ON movie_candidates(tconst)",
    )
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    call_sql = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "CREATE INDEX" in call_sql


@pytest.mark.asyncio
async def test_ensure_index_swallows_duplicate_key_error(mock_db_pool):
    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(1061, "Duplicate key name 'idx_test'")
    )
    # Should not raise
    await _ensure_index(
        mock_db_pool,
        "movie_candidates",
        "idx_test",
        "CREATE INDEX idx_test ON movie_candidates(tconst)",
    )


@pytest.mark.asyncio
async def test_ensure_index_reraises_other_errors(mock_db_pool):
    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(1146, "Table doesn't exist")
    )
    with pytest.raises(pymysql.err.OperationalError):
        await _ensure_index(
            mock_db_pool, "no_such", "idx_test", "CREATE INDEX idx_test ON no_such(x)"
        )


@pytest.mark.asyncio
async def test_ensure_column_swallows_duplicate_column_error(mock_db_pool):
    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(1060, "Duplicate column name 'extra'")
    )
    # Should not raise
    await _ensure_column(
        mock_db_pool,
        "movie_candidates",
        "extra",
        "ALTER TABLE movie_candidates ADD COLUMN extra INT",
    )


# ---------------------------------------------------------------------------
# Public wrapper tests (updated to new CREATE-directly shape)
# ---------------------------------------------------------------------------


async def test_ensure_user_navigation_current_ref_column_adds_when_missing(mock_db_pool):
    await ensure_user_navigation_current_ref_column(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    alter_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ADD COLUMN current_ref_json JSON NULL" in alter_query


async def test_ensure_movie_candidates_shuffle_key_adds_and_backfills(mock_db_pool):
    # DDL cursor: ADD COLUMN succeeds
    # db_pool.execute sequence:
    #   1. SELECT flag (None — not done)
    #   2. SELECT GET_LOCK → {"locked": 1}
    #   3. SELECT flag (re-check under lock, None)
    #   4. UPDATE chunk → returns 0 to exit the loop immediately
    #   5. ALTER
    #   6. INSERT flag
    #   7. SELECT RELEASE_LOCK
    mock_db_pool.execute.side_effect = [
        None,
        {"locked": 1},
        None,
        0,
        None,
        None,
        {"released": 1},
    ]

    await ensure_movie_candidates_shuffle_key(mock_db_pool)

    # DDL cursor used for ADD COLUMN
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    add_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ADD COLUMN shuffle_key INT NULL" in add_query

    queries = [call.args[0] for call in mock_db_pool.execute.await_args_list]
    assert "SELECT meta_value FROM runtime_metadata" in queries[0]
    assert "GET_LOCK" in queries[1]
    assert "SELECT meta_value FROM runtime_metadata" in queries[2]
    assert "UPDATE movie_candidates" in queries[3]
    assert "LIMIT %s" in queries[3]
    assert "MODIFY COLUMN shuffle_key INT NOT NULL" in queries[4]
    assert "INSERT INTO runtime_metadata" in queries[5]
    assert "RELEASE_LOCK" in queries[6]


async def test_ensure_movie_candidates_shuffle_key_skips_when_lock_not_acquired(mock_db_pool):
    """Another replica holds the backfill lock; we short-circuit cleanly."""
    # Sequence: SELECT flag (None), GET_LOCK → not acquired
    mock_db_pool.execute.side_effect = [None, {"locked": 0}]

    await ensure_movie_candidates_shuffle_key(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    assert mock_db_pool.execute.await_count == 2
    assert "GET_LOCK" in mock_db_pool.execute.await_args_list[1].args[0]


async def test_ensure_movie_candidates_shuffle_key_skips_when_flag_set(mock_db_pool):
    # DDL cursor: ADD COLUMN succeeds
    # db_pool.execute: SELECT flag → returns "1" → early return before lock
    mock_db_pool.execute.side_effect = [{"meta_value": "1"}]

    await ensure_movie_candidates_shuffle_key(mock_db_pool)

    # DDL cursor used for ADD COLUMN
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    add_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ADD COLUMN shuffle_key INT NULL" in add_query

    # Only the flag check via db_pool.execute — no lock, no UPDATE, no ALTER
    assert mock_db_pool.execute.await_count == 1
    flag_select = mock_db_pool.execute.await_args_list[0].args[0]
    assert "SELECT meta_value FROM runtime_metadata" in flag_select


async def test_ensure_movie_candidates_refreshed_at_index_adds_when_missing(mock_db_pool):
    await ensure_movie_candidates_refreshed_at_index(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    create_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "CREATE INDEX idx_movie_candidates_refreshed_at" in create_query


async def test_ensure_runtime_schema_runs_additive_repairs_including_fulltext(mock_db_pool):
    mock_db_pool.execute = AsyncMock()

    with (
        patch(
            "infra.runtime_schema.ensure_user_navigation_current_ref_column", AsyncMock()
        ) as ensure_current_ref,
        patch(
            "infra.runtime_schema.ensure_movie_candidates_shuffle_key", AsyncMock()
        ) as ensure_shuffle,
        patch(
            "infra.runtime_schema.ensure_movie_candidates_refreshed_at_index", AsyncMock()
        ) as ensure_refresh,
        patch(
            "infra.runtime_schema.ensure_user_navigation_user_id_column", AsyncMock()
        ) as ensure_user_id,
        patch(
            "infra.runtime_schema.ensure_movie_candidates_fulltext_index", AsyncMock()
        ) as ensure_fulltext,
        patch(
            "infra.runtime_schema.ensure_movie_candidates_shuffle_key_index", AsyncMock()
        ) as ensure_shuffle_idx,
        patch(
            "infra.runtime_schema.ensure_movie_candidates_bucket_filter_index", AsyncMock()
        ) as ensure_bucket_filter_idx,
        patch(
            "infra.runtime_schema.ensure_movie_projection_state_last_attempt_index", AsyncMock()
        ) as ensure_state_last_attempt_idx,
        patch(
            "infra.runtime_schema.ensure_users_exclude_watched_default_column", AsyncMock()
        ) as ensure_users_exclude,
    ):
        await ensure_runtime_schema(mock_db_pool)

    assert mock_db_pool.execute.await_count == len(_RUNTIME_SCHEMA_STATEMENTS)
    ensure_current_ref.assert_awaited_once_with(mock_db_pool)
    ensure_shuffle.assert_awaited_once_with(mock_db_pool)
    ensure_refresh.assert_awaited_once_with(mock_db_pool)
    ensure_user_id.assert_awaited_once_with(mock_db_pool)
    ensure_shuffle_idx.assert_awaited_once_with(mock_db_pool)
    ensure_bucket_filter_idx.assert_awaited_once_with(mock_db_pool)
    ensure_state_last_attempt_idx.assert_awaited_once_with(mock_db_pool)
    ensure_users_exclude.assert_awaited_once_with(mock_db_pool)
    ensure_fulltext.assert_awaited_once_with(mock_db_pool)


async def test_ensure_runtime_schema_skips_existing_table_ddl(mock_db_pool):
    mock_db_pool.execute = AsyncMock(return_value={"present": 1})

    with patched_runtime_schema_repairs():
        await ensure_runtime_schema(mock_db_pool)

    assert mock_db_pool.execute.await_count == len(_RUNTIME_SCHEMA_TABLES)
    table_names = []
    for call in mock_db_pool.execute.await_args_list:
        query, params = call.args[:2]
        assert "information_schema.tables" in query.lower()
        assert call.kwargs["fetch"] == "one"
        table_names.append(params[0])

    assert table_names == _RUNTIME_SCHEMA_TABLES
    mock_db_pool._ddl_cursor.execute.assert_not_awaited()


async def test_ensure_runtime_schema_surfaces_table_create_failures(mock_db_pool):
    mock_db_pool.execute = AsyncMock(return_value=None)
    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(1142, "CREATE command denied")
    )

    with patched_runtime_schema_repairs():
        with pytest.raises(pymysql.err.OperationalError):
            await ensure_runtime_schema(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()


async def test_ensure_movie_candidates_bucket_filter_index_adds_when_missing(mock_db_pool):
    await ensure_movie_candidates_bucket_filter_index(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    create_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "CREATE INDEX idx_movie_candidates_bucket_filter" in create_query
    assert "(titleType, sample_bucket, numVotes, averageRating, startYear)" in create_query


async def test_ensure_movie_candidates_shuffle_key_index_adds_when_missing(mock_db_pool):
    from infra.runtime_schema import ensure_movie_candidates_shuffle_key_index

    await ensure_movie_candidates_shuffle_key_index(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    create_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "CREATE INDEX idx_movie_candidates_shuffle" in create_query
    assert "(shuffle_key, numVotes, averageRating)" in create_query


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
    assert any(
        "idx_watchlist_user_added" in sql
        for sql in ddl_calls
        if "user_watchlist" in sql
    )


async def test_ensure_movie_candidates_shuffle_key_index_skips_when_present(mock_db_pool):
    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(
            1061, "Duplicate key name 'idx_movie_candidates_shuffle'"
        )
    )

    from infra.runtime_schema import ensure_movie_candidates_shuffle_key_index

    await ensure_movie_candidates_shuffle_key_index(mock_db_pool)

    # DDL cursor still issued the CREATE; the duplicate-key error is swallowed.
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    assert "CREATE INDEX" in mock_db_pool._ddl_cursor.execute.call_args[0][0]


async def test_ensure_runtime_schema_creates_users_table(mock_db_pool):
    from infra.runtime_schema import _RUNTIME_SCHEMA_STATEMENTS

    users_sql = [s for s in _RUNTIME_SCHEMA_STATEMENTS if "CREATE TABLE users" in s]
    assert len(users_sql) == 1
    assert "user_id" in users_sql[0]
    assert "email" in users_sql[0]
    assert "password_hash" in users_sql[0]


async def test_ensure_runtime_schema_creates_users_table_with_exclude_watched_default(
    mock_db_pool,
):
    users_sql = [s for s in _RUNTIME_SCHEMA_STATEMENTS if "CREATE TABLE users" in s]
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


async def test_ensure_runtime_schema_creates_watched_table(mock_db_pool):
    from infra.runtime_schema import _RUNTIME_SCHEMA_STATEMENTS

    watched_sql = [s for s in _RUNTIME_SCHEMA_STATEMENTS if "CREATE TABLE user_watched_movies" in s]
    assert len(watched_sql) == 1
    assert "user_id" in watched_sql[0]
    assert "tconst" in watched_sql[0]


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


async def test_ensure_users_exclude_watchlist_default_column_runs_alter(mock_db_pool):
    from infra.runtime_schema import ensure_users_exclude_watchlist_default_column

    await ensure_users_exclude_watchlist_default_column(mock_db_pool)
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    sql = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ALTER TABLE users" in sql
    assert "ADD COLUMN exclude_watchlist_default BOOLEAN NOT NULL DEFAULT TRUE" in sql


async def test_ensure_users_exclude_watchlist_default_column_skips_when_present(mock_db_pool):
    from infra.runtime_schema import ensure_users_exclude_watchlist_default_column

    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(
            1060, "Duplicate column name 'exclude_watchlist_default'"
        )
    )
    # Must NOT raise — duplicate-column errno is the idempotent signal.
    await ensure_users_exclude_watchlist_default_column(mock_db_pool)


async def test_ensure_runtime_schema_creates_letterboxd_imports_table(mock_db_pool):
    from infra.runtime_schema import _RUNTIME_SCHEMA_STATEMENTS

    matches = [s for s in _RUNTIME_SCHEMA_STATEMENTS if "CREATE TABLE letterboxd_imports" in s]
    assert len(matches) == 1
    sql = matches[0]
    for col in (
        "import_id",
        "user_id",
        "status",
        "total_rows",
        "processed",
        "matched",
        "skipped",
        "failed",
        "error_message",
        "created_at",
        "updated_at",
        "completed_at",
    ):
        assert col in sql, f"missing column {col} in letterboxd_imports DDL"
    assert "PRIMARY KEY (import_id)" in sql or "import_id     CHAR(32) PRIMARY KEY" in sql
    assert "idx_letterboxd_user_created" in sql
