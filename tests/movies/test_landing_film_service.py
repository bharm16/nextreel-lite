"""Tests for movies.landing_film_service.fetch_random_landing_film."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from movies.landing_film_service import fetch_random_landing_film, _clean


def test_clean_returns_value_for_real_strings():
    assert _clean("Wong Kar-wai") == "Wong Kar-wai"
    assert _clean("102 min") == "102 min"
    assert _clean("1994") == "1994"


def test_clean_returns_none_for_sentinels():
    assert _clean(None) is None
    assert _clean("") is None
    assert _clean("Unknown") is None
    assert _clean("N/A") is None
    assert _clean("0 min") is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_pool_empty():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[])
    result = await fetch_random_landing_film(pool)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_flat_dict_from_row_with_dict_payload():
    """payload_json comes back already-parsed as a dict from aiomysql (recent drivers)."""
    pool = AsyncMock()
    pool.execute = AsyncMock(
        return_value=[
            {
                "tconst": "tt0109424",
                "payload_json": {
                    "title": "Chungking Express",
                    "year": "1994",
                    "directors": "Wong Kar-wai",
                    "runtime": "102 min",
                    "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
                },
            }
        ]
    )
    result = await fetch_random_landing_film(pool)
    assert result == {
        "tconst": "tt0109424",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }


@pytest.mark.asyncio
async def test_fetch_returns_flat_dict_from_row_with_string_payload():
    """payload_json comes back as a JSON-encoded string from some driver versions."""
    pool = AsyncMock()
    pool.execute = AsyncMock(
        return_value=[
            {
                "tconst": "tt0118694",
                "payload_json": json.dumps(
                    {
                        "title": "In the Mood for Love",
                        "year": "2000",
                        "directors": "Wong Kar-wai",
                        "runtime": "98 min",
                        "backdrop_url": "https://image.tmdb.org/t/p/original/bar.jpg",
                    }
                ),
            }
        ]
    )
    result = await fetch_random_landing_film(pool)
    assert result["title"] == "In the Mood for Love"
    assert result["director"] == "Wong Kar-wai"


@pytest.mark.asyncio
async def test_fetch_scrubs_sentinel_values_for_missing_metadata():
    pool = AsyncMock()
    pool.execute = AsyncMock(
        return_value=[
            {
                "tconst": "tt000001",
                "payload_json": {
                    "title": "Partial Record",
                    "year": "N/A",
                    "directors": "Unknown",
                    "runtime": "0 min",
                    "backdrop_url": "https://image.tmdb.org/t/p/original/x.jpg",
                },
            }
        ]
    )
    result = await fetch_random_landing_film(pool)
    assert result["title"] == "Partial Record"
    assert result["year"] is None
    assert result["director"] is None
    assert result["runtime"] is None
    assert result["backdrop_url"] == "https://image.tmdb.org/t/p/original/x.jpg"


@pytest.mark.asyncio
async def test_fetch_sql_filters_to_ready_state_with_tmdb_backdrop():
    """The SQL must restrict to READY + TMDb-sourced backdrops."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[])
    await fetch_random_landing_film(pool)
    # First positional arg to pool.execute is the SQL
    sql = pool.execute.call_args.args[0]
    assert "movie_projection" in sql
    assert "projection_state = 'ready'" in sql
    assert "image.tmdb.org" in sql
    assert "ORDER BY RAND()" in sql
    assert "LIMIT 1" in sql
