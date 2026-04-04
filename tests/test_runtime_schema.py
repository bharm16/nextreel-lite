"""Tests for runtime schema verification and repair helpers."""

from unittest.mock import AsyncMock, patch

from infra.runtime_schema import (
    _RUNTIME_SCHEMA_STATEMENTS,
    ensure_movie_candidates_fulltext_index,
    ensure_movie_candidates_refreshed_at_index,
    ensure_movie_candidates_shuffle_key,
    ensure_runtime_schema,
    ensure_user_navigation_current_ref_column,
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
    assert "ALTER TABLE movie_candidates ADD FULLTEXT KEY ftx_movie_candidates_genres (genres)" in alter_query


async def test_ensure_user_navigation_current_ref_column_adds_when_missing(mock_db_pool):
    mock_db_pool.execute.side_effect = [None, None]

    await ensure_user_navigation_current_ref_column(mock_db_pool)

    assert mock_db_pool.execute.await_count == 2
    lookup_query = mock_db_pool.execute.await_args_list[0].args[0]
    alter_query = mock_db_pool.execute.await_args_list[1].args[0]
    assert "information_schema.columns" in lookup_query
    assert "ADD COLUMN current_ref_json JSON NULL" in alter_query


async def test_ensure_movie_candidates_shuffle_key_adds_and_backfills(mock_db_pool):
    mock_db_pool.execute.side_effect = [None, None, None, None]

    await ensure_movie_candidates_shuffle_key(mock_db_pool)

    assert mock_db_pool.execute.await_count == 4
    lookup_query = mock_db_pool.execute.await_args_list[0].args[0]
    add_query = mock_db_pool.execute.await_args_list[1].args[0]
    update_query = mock_db_pool.execute.await_args_list[2].args[0]
    alter_query = mock_db_pool.execute.await_args_list[3].args[0]
    assert "information_schema.columns" in lookup_query
    assert "ADD COLUMN shuffle_key INT NULL" in add_query
    assert "UPDATE movie_candidates" in update_query
    assert "MODIFY COLUMN shuffle_key INT NOT NULL" in alter_query


async def test_ensure_movie_candidates_refreshed_at_index_adds_when_missing(mock_db_pool):
    mock_db_pool.execute.side_effect = [None, None]

    await ensure_movie_candidates_refreshed_at_index(mock_db_pool)

    assert mock_db_pool.execute.await_count == 2
    lookup_query = mock_db_pool.execute.await_args_list[0].args[0]
    create_query = mock_db_pool.execute.await_args_list[1].args[0]
    assert "information_schema.statistics" in lookup_query
    assert "CREATE INDEX idx_movie_candidates_refreshed_at" in create_query


async def test_ensure_runtime_schema_runs_additive_repairs_without_blocking_fulltext(mock_db_pool):
    mock_db_pool.execute = AsyncMock()

    with patch("infra.runtime_schema.ensure_user_navigation_current_ref_column", AsyncMock()) as ensure_current_ref, \
         patch("infra.runtime_schema.ensure_movie_candidates_shuffle_key", AsyncMock()) as ensure_shuffle, \
         patch("infra.runtime_schema.ensure_movie_candidates_refreshed_at_index", AsyncMock()) as ensure_refresh, \
         patch("infra.runtime_schema.ensure_movie_candidates_fulltext_index", AsyncMock()) as ensure_fulltext:
        await ensure_runtime_schema(mock_db_pool)

    assert mock_db_pool.execute.await_count == len(_RUNTIME_SCHEMA_STATEMENTS)
    ensure_current_ref.assert_awaited_once_with(mock_db_pool)
    ensure_shuffle.assert_awaited_once_with(mock_db_pool)
    ensure_refresh.assert_awaited_once_with(mock_db_pool)
    ensure_fulltext.assert_not_called()
