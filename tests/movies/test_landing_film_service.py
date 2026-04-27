"""Tests for movies.landing_film_service.fetch_random_landing_film."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from movies.landing_film_service import (
    _clean,
    _reset_ready_count_cache,
    fetch_random_landing_film,
)


@pytest.fixture(autouse=True)
def _clear_ready_count_cache():
    """Reset the module-level count cache between tests so each sees a cold load."""
    _reset_ready_count_cache()
    yield
    _reset_ready_count_cache()


def _make_pool(*, count: int, rows: list[dict] | None):
    """Build an AsyncMock pool whose execute() returns count, then rows."""
    pool = AsyncMock()

    async def _execute(sql, *args, **kwargs):
        if "COUNT(*)" in sql:
            return {"n": count}
        return rows if rows is not None else []

    pool.execute = AsyncMock(side_effect=_execute)
    return pool


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
    pool = _make_pool(count=0, rows=None)
    result = await fetch_random_landing_film(pool)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_count_positive_but_no_rows():
    pool = _make_pool(count=100, rows=[])
    result = await fetch_random_landing_film(pool)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_flat_dict_from_row_with_dict_payload():
    """payload_json comes back already-parsed as a dict from aiomysql (recent drivers)."""
    pool = _make_pool(
        count=10,
        rows=[
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
        ],
    )
    result = await fetch_random_landing_film(pool)
    assert result == {
        "tconst": "tt0109424",
        "public_id": None,
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }


@pytest.mark.asyncio
async def test_fetch_returns_flat_dict_from_row_with_string_payload():
    """payload_json comes back as a JSON-encoded string from some driver versions."""
    pool = _make_pool(
        count=10,
        rows=[
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
        ],
    )
    result = await fetch_random_landing_film(pool)
    assert result["title"] == "In the Mood for Love"
    assert result["director"] == "Wong Kar-wai"


@pytest.mark.asyncio
async def test_fetch_scrubs_sentinel_values_for_missing_metadata():
    pool = _make_pool(
        count=10,
        rows=[
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
        ],
    )
    result = await fetch_random_landing_film(pool)
    assert result["title"] == "Partial Record"
    assert result["year"] is None
    assert result["director"] is None
    assert result["runtime"] is None
    assert result["backdrop_url"] == "https://image.tmdb.org/t/p/original/x.jpg"


@pytest.mark.asyncio
async def test_fetch_skips_rows_with_non_tmdb_backdrop():
    """Rows whose backdrop doesn't live on image.tmdb.org are filtered out in Python."""
    pool = _make_pool(
        count=10,
        rows=[
            {
                "tconst": "tt_no_backdrop",
                "payload_json": {"title": "Missing", "backdrop_url": None},
            },
            {
                "tconst": "tt_wrong_host",
                "payload_json": {
                    "title": "Wrong host",
                    "backdrop_url": "https://example.com/foo.jpg",
                },
            },
            {
                "tconst": "tt_ok",
                "payload_json": {
                    "title": "Good one",
                    "backdrop_url": "https://image.tmdb.org/t/p/original/ok.jpg",
                },
            },
        ],
    )
    result = await fetch_random_landing_film(pool)
    assert result is not None
    assert result["tconst"] == "tt_ok"


@pytest.mark.asyncio
async def test_fetch_returns_none_when_no_row_has_tmdb_backdrop():
    pool = _make_pool(
        count=10,
        rows=[
            {
                "tconst": "tt_none",
                "payload_json": {"title": "None", "backdrop_url": None},
            },
            {
                "tconst": "tt_wrong",
                "payload_json": {
                    "title": "Wrong",
                    "backdrop_url": "https://example.com/foo.jpg",
                },
            },
        ],
    )
    result = await fetch_random_landing_film(pool)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_uses_ready_state_filter_and_limit_offset_not_rand():
    """READY-state SELECT with LIMIT/OFFSET — no RAND() or JSON_UNQUOTE.

    The fetch is split into two queries: an id-only SELECT with LIMIT/OFFSET
    (keeps the filesort narrow), then a payload fetch using WHERE tconst IN
    (...). Together they must cover all the invariants below.
    """
    pool = _make_pool(count=100, rows=[])
    await fetch_random_landing_film(pool)
    non_count_sqls = [
        c.args[0] for c in pool.execute.await_args_list if "COUNT(*)" not in c.args[0]
    ]
    assert non_count_sqls, "expected at least one non-count SELECT"
    # The id-only keyset SELECT carries the READY filter and LIMIT/OFFSET.
    id_sql = next(
        (s for s in non_count_sqls if "LIMIT %s OFFSET %s" in s),
        None,
    )
    assert id_sql is not None, "expected a LIMIT/OFFSET SELECT"
    assert "movie_projection" in id_sql
    assert "projection_state = 'ready'" in id_sql
    # No filesort on the wide payload_json column and no JSON extraction in
    # the hot landing path — global invariants across every issued query.
    for sql in non_count_sqls:
        assert "ORDER BY RAND()" not in sql
        assert "JSON_UNQUOTE" not in sql


@pytest.mark.asyncio
async def test_fetch_skips_count_on_second_call_when_cached():
    """The READY-row count is cached in-process across calls within the TTL."""
    pool = _make_pool(count=100, rows=[])
    await fetch_random_landing_film(pool)
    count_calls_1 = sum(1 for c in pool.execute.await_args_list if "COUNT(*)" in c.args[0])
    await fetch_random_landing_film(pool)
    count_calls_2 = sum(1 for c in pool.execute.await_args_list if "COUNT(*)" in c.args[0])
    assert count_calls_1 == 1
    assert count_calls_2 == 1  # still 1 — second call hit the cache


async def test_landing_film_query_selects_public_id():
    from unittest.mock import AsyncMock
    from movies.landing_film_service import fetch_random_landing_film

    pool = AsyncMock()
    pool.execute = AsyncMock(
        side_effect=[
            {"n": 1},                     # COUNT query result
            [{"tconst": "tt0000001"}],    # id-only SELECT result
            None,                          # payload SELECT — return None, function exits gracefully
        ]
    )

    await fetch_random_landing_film(pool)

    all_sqls = [call.args[0] for call in pool.execute.await_args_list]
    assert any("public_id" in sql for sql in all_sqls)
