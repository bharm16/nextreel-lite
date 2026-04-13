from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def test_movie_count_cache_lives_outside_random_fetcher():
    from movies.movie_count_cache import MovieCountCache

    assert MovieCountCache.__module__ == "movies.movie_count_cache"


def test_random_movie_fetcher_owns_count_cache_collaborator():
    from movies.movie_count_cache import MovieCountCache
    from movies.query_builder import ImdbRandomMovieFetcher

    fetcher = ImdbRandomMovieFetcher(AsyncMock(), cache=AsyncMock())

    assert isinstance(fetcher._count_cache, MovieCountCache)


@pytest.mark.asyncio
async def test_fetcher_delegates_counting_to_count_cache(monkeypatch):
    from movies.query_builder import ImdbRandomMovieFetcher

    db_pool = AsyncMock()
    db_pool.execute = AsyncMock(return_value=[{"tconst": "tt1"}])
    fetcher = ImdbRandomMovieFetcher(db_pool)
    fetcher._count_cache.count_qualifying_rows = AsyncMock(return_value=10)

    await fetcher.fetch_random_movies({"language": "any"}, limit=1)

    fetcher._count_cache.count_qualifying_rows.assert_awaited_once()
