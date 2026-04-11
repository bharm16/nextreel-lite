"""Tests for runtime schema verification and repair helpers."""

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


async def test_ensure_movie_candidates_fulltext_index_skips_repair_when_present(mock_db_pool):
    mock_db_pool.execute.return_value = {"present": 1}

    await ensure_movie_candidates_fulltext_index(mock_db_pool)

    mock_db_pool.execute.assert_awaited_once()
    query, params = mock_db_pool.execute.await_args.args[:2]
    assert "information_schema.statistics" in query
    assert params == ["ftx_movie_candidates_genres"]


async def test_ensure_movie_candidates_fulltext_index_repairs_when_missing(mock_db_pool):
    mock_db_pool.execute.side_effect = [None, None]

    await ensure_movie_candidates_fulltext_index(mock_db_pool)

    assert mock_db_pool.execute.await_count == 2
    lookup_query = mock_db_pool.execute.await_args_list[0].args[0]
    alter_query = mock_db_pool.execute.await_args_list[1].args[0]
    assert "information_schema.statistics" in lookup_query
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
    # db_pool.execute: SELECT flag (None), UPDATE, ALTER, INSERT flag
    mock_db_pool.execute.side_effect = [None, None, None, None]

    await ensure_movie_candidates_shuffle_key(mock_db_pool)

    # DDL cursor used for ADD COLUMN
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    add_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ADD COLUMN shuffle_key INT NULL" in add_query

    # db_pool.execute used for flag check + backfill
    assert mock_db_pool.execute.await_count == 4
    flag_select = mock_db_pool.execute.await_args_list[0].args[0]
    update_query = mock_db_pool.execute.await_args_list[1].args[0]
    alter_query = mock_db_pool.execute.await_args_list[2].args[0]
    flag_insert = mock_db_pool.execute.await_args_list[3].args[0]
    assert "SELECT meta_value FROM runtime_metadata" in flag_select
    assert "UPDATE movie_candidates" in update_query
    assert "MODIFY COLUMN shuffle_key INT NOT NULL" in alter_query
    assert "INSERT INTO runtime_metadata" in flag_insert


async def test_ensure_movie_candidates_shuffle_key_skips_when_flag_set(mock_db_pool):
    # DDL cursor: ADD COLUMN succeeds
    # db_pool.execute: SELECT flag → returns "1" → early return
    mock_db_pool.execute.side_effect = [{"meta_value": "1"}]

    await ensure_movie_candidates_shuffle_key(mock_db_pool)

    # DDL cursor used for ADD COLUMN
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    add_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "ADD COLUMN shuffle_key INT NULL" in add_query

    # Only the flag check via db_pool.execute, no UPDATE/ALTER
    assert mock_db_pool.execute.await_count == 1
    flag_select = mock_db_pool.execute.await_args_list[0].args[0]
    assert "SELECT meta_value FROM runtime_metadata" in flag_select


async def test_ensure_movie_candidates_refreshed_at_index_adds_when_missing(mock_db_pool):
    await ensure_movie_candidates_refreshed_at_index(mock_db_pool)

    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    create_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "CREATE INDEX idx_movie_candidates_refreshed_at" in create_query


async def test_ensure_runtime_schema_runs_additive_repairs_without_blocking_fulltext(mock_db_pool):
    mock_db_pool.execute = AsyncMock()

    with patch(
        "infra.runtime_schema.ensure_user_navigation_current_ref_column", AsyncMock()
    ) as ensure_current_ref, patch(
        "infra.runtime_schema.ensure_movie_candidates_shuffle_key", AsyncMock()
    ) as ensure_shuffle, patch(
        "infra.runtime_schema.ensure_movie_candidates_refreshed_at_index", AsyncMock()
    ) as ensure_refresh, patch(
        "infra.runtime_schema.ensure_user_navigation_user_id_column", AsyncMock()
    ) as ensure_user_id, patch(
        "infra.runtime_schema.ensure_movie_candidates_fulltext_index", AsyncMock()
    ) as ensure_fulltext, patch(
        "infra.runtime_schema.ensure_movie_candidates_shuffle_key_index", AsyncMock()
    ) as ensure_shuffle_idx, patch(
        "infra.runtime_schema.ensure_movie_candidates_bucket_filter_index", AsyncMock()
    ) as ensure_bucket_filter_idx, patch(
        "infra.runtime_schema.ensure_popular_movies_cache_composite_index", AsyncMock()
    ) as ensure_cache_composite_idx, patch(
        "infra.runtime_schema.ensure_users_exclude_watched_default_column", AsyncMock()
    ) as ensure_users_exclude:
        await ensure_runtime_schema(mock_db_pool)

    assert mock_db_pool.execute.await_count == len(_RUNTIME_SCHEMA_STATEMENTS)
    ensure_current_ref.assert_awaited_once_with(mock_db_pool)
    ensure_shuffle.assert_awaited_once_with(mock_db_pool)
    ensure_refresh.assert_awaited_once_with(mock_db_pool)
    ensure_user_id.assert_awaited_once_with(mock_db_pool)
    ensure_shuffle_idx.assert_awaited_once_with(mock_db_pool)
    ensure_bucket_filter_idx.assert_awaited_once_with(mock_db_pool)
    ensure_cache_composite_idx.assert_awaited_once_with(mock_db_pool)
    ensure_users_exclude.assert_awaited_once_with(mock_db_pool)
    ensure_fulltext.assert_not_called()


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

    users_sql = [s for s in _RUNTIME_SCHEMA_STATEMENTS if "CREATE TABLE IF NOT EXISTS users" in s]
    assert len(users_sql) == 1
    assert "user_id" in users_sql[0]
    assert "email" in users_sql[0]
    assert "password_hash" in users_sql[0]


async def test_ensure_runtime_schema_creates_users_table_with_exclude_watched_default(
    mock_db_pool,
):
    users_sql = [
        s for s in _RUNTIME_SCHEMA_STATEMENTS if "CREATE TABLE IF NOT EXISTS users" in s
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


async def test_ensure_runtime_schema_creates_watched_table(mock_db_pool):
    from infra.runtime_schema import _RUNTIME_SCHEMA_STATEMENTS

    watched_sql = [
        s
        for s in _RUNTIME_SCHEMA_STATEMENTS
        if "CREATE TABLE IF NOT EXISTS user_watched_movies" in s
    ]
    assert len(watched_sql) == 1
    assert "user_id" in watched_sql[0]
    assert "tconst" in watched_sql[0]


async def test_ensure_popular_movies_cache_composite_index_skips_when_table_missing(mock_db_pool):
    mock_db_pool.execute.return_value = None  # table existence check returns None

    from infra.runtime_schema import ensure_popular_movies_cache_composite_index

    await ensure_popular_movies_cache_composite_index(mock_db_pool)

    assert mock_db_pool.execute.await_count == 1
    lookup_query = mock_db_pool.execute.await_args.args[0]
    assert "information_schema.tables" in lookup_query.lower()


async def test_ensure_popular_movies_cache_composite_index_skips_when_index_present(mock_db_pool):
    # Table-existence probe returns a row via db_pool.execute.
    mock_db_pool.execute.return_value = {"present": 1}
    # CREATE INDEX raises duplicate-key via DDL cursor.
    mock_db_pool._ddl_cursor.execute = AsyncMock(
        side_effect=pymysql.err.OperationalError(
            1061, "Duplicate key name 'idx_cache_filter_rand'"
        )
    )

    from infra.runtime_schema import ensure_popular_movies_cache_composite_index

    await ensure_popular_movies_cache_composite_index(mock_db_pool)

    # First call is the table-existence probe via db_pool.execute.
    assert mock_db_pool.execute.await_count == 1
    assert "information_schema.tables" in mock_db_pool.execute.await_args.args[0].lower()
    # DDL cursor received the CREATE INDEX attempt that errored with duplicate key.
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    assert "CREATE INDEX" in mock_db_pool._ddl_cursor.execute.call_args[0][0]


async def test_ensure_popular_movies_cache_composite_index_creates_when_missing(mock_db_pool):
    # Table exists via db_pool.execute, CREATE INDEX succeeds via DDL cursor.
    mock_db_pool.execute.return_value = {"present": 1}

    from infra.runtime_schema import ensure_popular_movies_cache_composite_index

    await ensure_popular_movies_cache_composite_index(mock_db_pool)

    assert mock_db_pool.execute.await_count == 1
    mock_db_pool._ddl_cursor.execute.assert_awaited_once()
    create_query = mock_db_pool._ddl_cursor.execute.call_args[0][0]
    assert "CREATE INDEX idx_cache_filter_rand" in create_query
    assert "popular_movies_cache" in create_query
    assert "(startYear, averageRating, numVotes, rand_order)" in create_query
