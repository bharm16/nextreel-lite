from __future__ import annotations

import pytest
from unittest.mock import patch

from session.user_preferences import (
    clear_default_filters,
    get_default_filters,
    get_exclude_watched_default,
    get_exclude_watchlist_default,
    get_theme_preference,
    set_default_filters,
    set_exclude_watched_default,
    set_exclude_watchlist_default,
    set_theme_preference,
)


@pytest.mark.asyncio
async def test_get_exclude_watched_default_returns_true_when_user_row_missing(mock_db_pool):
    mock_db_pool.execute.return_value = None

    result = await get_exclude_watched_default(mock_db_pool, "user-123")

    assert result is True
    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "WHERE user_id = %s" in query
    assert params == ["user-123"]
    assert call[1]["fetch"] == "one"


@pytest.mark.asyncio
async def test_get_exclude_watched_default_returns_false_from_row(mock_db_pool):
    mock_db_pool.execute.return_value = {"exclude_watched_default": 0}

    result = await get_exclude_watched_default(mock_db_pool, "user-123")

    assert result is False
    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "WHERE user_id = %s" in query
    assert params == ["user-123"]
    assert call[1]["fetch"] == "one"


@pytest.mark.asyncio
async def test_get_exclude_watched_default_returns_true_from_row(mock_db_pool):
    mock_db_pool.execute.return_value = {"exclude_watched_default": 1}

    result = await get_exclude_watched_default(mock_db_pool, "user-123")

    assert result is True
    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "WHERE user_id = %s" in query
    assert params == ["user-123"]
    assert call[1]["fetch"] == "one"


@pytest.mark.asyncio
async def test_set_exclude_watched_default_updates_value_and_timestamp(mock_db_pool):
    mock_db_pool.execute.return_value = None

    sentinel_timestamp = object()
    with patch("session.user_preferences.utcnow", return_value=sentinel_timestamp):
        await set_exclude_watched_default(mock_db_pool, "user-123", False)

    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "UPDATE users" in query
    assert "WHERE user_id = %s" in query
    assert params[0] is False
    assert params[1] is sentinel_timestamp
    assert params[2] == "user-123"
    assert call[1]["fetch"] == "none"


@pytest.mark.asyncio
async def test_get_exclude_watchlist_default_returns_true_when_user_row_missing(mock_db_pool):
    mock_db_pool.execute.return_value = None

    result = await get_exclude_watchlist_default(mock_db_pool, "user-123")

    assert result is True
    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "WHERE user_id = %s" in query
    assert params == ["user-123"]
    assert call[1]["fetch"] == "one"


@pytest.mark.asyncio
async def test_get_exclude_watchlist_default_returns_false_from_row(mock_db_pool):
    mock_db_pool.execute.return_value = {"exclude_watchlist_default": 0}

    result = await get_exclude_watchlist_default(mock_db_pool, "user-123")

    assert result is False
    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "WHERE user_id = %s" in query
    assert params == ["user-123"]
    assert call[1]["fetch"] == "one"


@pytest.mark.asyncio
async def test_set_exclude_watchlist_default_updates_value_and_timestamp(mock_db_pool):
    mock_db_pool.execute.return_value = None

    sentinel_timestamp = object()
    with patch("session.user_preferences.utcnow", return_value=sentinel_timestamp):
        await set_exclude_watchlist_default(mock_db_pool, "user-123", False)

    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "UPDATE users" in query
    assert "WHERE user_id = %s" in query
    assert params[0] is False
    assert params[1] is sentinel_timestamp
    assert params[2] == "user-123"
    assert call[1]["fetch"] == "none"


# ---------------------------------------------------------------------------
# Theme preference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_theme_preference_returns_value(mock_db_pool):
    mock_db_pool.execute.return_value = {"theme_preference": "dark"}
    assert await get_theme_preference(mock_db_pool, "u1") == "dark"


@pytest.mark.asyncio
async def test_get_theme_preference_returns_none_when_unset(mock_db_pool):
    mock_db_pool.execute.return_value = {"theme_preference": None}
    assert await get_theme_preference(mock_db_pool, "u1") is None


@pytest.mark.asyncio
async def test_get_theme_preference_returns_none_for_unknown_value(mock_db_pool):
    mock_db_pool.execute.return_value = {"theme_preference": "rainbow"}
    assert await get_theme_preference(mock_db_pool, "u1") is None


@pytest.mark.asyncio
async def test_set_theme_preference_rejects_unknown_value(mock_db_pool):
    with pytest.raises(ValueError):
        await set_theme_preference(mock_db_pool, "u1", "rainbow")


@pytest.mark.asyncio
async def test_set_theme_preference_writes_valid_value(mock_db_pool):
    await set_theme_preference(mock_db_pool, "u1", "light")
    call = mock_db_pool.execute.call_args
    query = call[0][0]
    params = call[0][1]
    assert "UPDATE users" in query
    assert "theme_preference" in query
    assert params[0] == "light"
    assert params[-1] == "u1"


@pytest.mark.asyncio
async def test_set_theme_preference_accepts_none(mock_db_pool):
    await set_theme_preference(mock_db_pool, "u1", None)
    params = mock_db_pool.execute.call_args[0][1]
    assert params[0] is None


# ---------------------------------------------------------------------------
# Default filter presets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_default_filters_returns_parsed_dict(mock_db_pool):
    import json as _json
    mock_db_pool.execute.return_value = {
        "default_filters_json": _json.dumps({"genres": ["Horror"], "min_year": 2000})
    }
    result = await get_default_filters(mock_db_pool, "u1")
    assert result == {"genres": ["Horror"], "min_year": 2000}


@pytest.mark.asyncio
async def test_get_default_filters_returns_none_when_unset(mock_db_pool):
    mock_db_pool.execute.return_value = {"default_filters_json": None}
    assert await get_default_filters(mock_db_pool, "u1") is None


@pytest.mark.asyncio
async def test_get_default_filters_passes_through_dict(mock_db_pool):
    # Some drivers return JSON columns already-parsed.
    mock_db_pool.execute.return_value = {
        "default_filters_json": {"genres": ["Action"]}
    }
    assert await get_default_filters(mock_db_pool, "u1") == {"genres": ["Action"]}


@pytest.mark.asyncio
async def test_get_default_filters_tolerates_malformed_json(mock_db_pool):
    mock_db_pool.execute.return_value = {"default_filters_json": "{ not json"}
    assert await get_default_filters(mock_db_pool, "u1") is None


@pytest.mark.asyncio
async def test_set_default_filters_serializes_to_json(mock_db_pool):
    import json as _json
    payload = {"genres": ["Horror", "Thriller"], "min_rating": 7.0}
    await set_default_filters(mock_db_pool, "u1", payload)
    params = mock_db_pool.execute.call_args[0][1]
    assert _json.loads(params[0]) == payload


@pytest.mark.asyncio
async def test_clear_default_filters_writes_null(mock_db_pool):
    await clear_default_filters(mock_db_pool, "u1")
    params = mock_db_pool.execute.call_args[0][1]
    assert params[0] is None
