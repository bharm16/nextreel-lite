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
