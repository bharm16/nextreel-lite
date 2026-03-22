from unittest.mock import AsyncMock

import pytest

from database.errors import DatabaseError
from database.pool import DatabaseConnectionPool
from scripts.filter_backend import ImdbRandomMovieFetcher
from scripts.movie import Movie


@pytest.mark.asyncio
async def test_database_connection_pool_wraps_backend_errors():
    db_pool = DatabaseConnectionPool(
        {"host": "localhost", "port": 3306, "user": "u", "password": "p", "database": "d"}
    )
    db_pool.pool.execute_secure = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(DatabaseError):
        await db_pool.execute("SELECT 1", fetch="one")


@pytest.mark.asyncio
async def test_fetch_random_movies_returns_empty_on_database_error():
    db_pool = AsyncMock()
    db_pool.execute = AsyncMock(side_effect=DatabaseError("boom"))
    fetcher = ImdbRandomMovieFetcher(db_pool)

    result = await fetcher.fetch_random_movies({"min_year": 2024, "language": "en"}, limit=5)

    assert result == []


@pytest.mark.asyncio
async def test_fetch_movie_ratings_returns_none_on_database_error():
    db_pool = AsyncMock()
    db_pool.execute = AsyncMock(side_effect=DatabaseError("boom"))
    movie = Movie("tt1234567", db_pool, tmdb_helper=AsyncMock())

    ratings = await movie.fetch_movie_ratings("tt1234567")

    assert ratings is None
