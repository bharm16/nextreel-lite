"""Tests for Movie.get_movie_data — verifies the combined-fetch flow."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from movies.movie import Movie
from tests.test_tmdb_parsers import _sample_combined_response


@pytest.fixture
def mock_tmdb():
    """TMDbHelper mock with get_movie_full returning sample data."""
    helper = MagicMock()
    helper.image_base_url = "https://image.tmdb.org/t/p/"
    helper.get_tmdb_id_by_tconst = AsyncMock(return_value=550)
    helper.get_movie_full = AsyncMock(return_value=_sample_combined_response())

    # Wire up real parse methods so we test integration
    from movies.tmdb_client import TMDbHelper

    real = TMDbHelper("key")
    helper.parse_cast = real.parse_cast
    helper.parse_directors = real.parse_directors
    helper.parse_key_crew = real.parse_key_crew
    helper.parse_trailer = real.parse_trailer
    helper.parse_images = real.parse_images
    helper.parse_age_rating = real.parse_age_rating
    helper.parse_watch_providers = real.parse_watch_providers
    helper.parse_keywords = real.parse_keywords
    helper.parse_recommendations = real.parse_recommendations
    helper.parse_external_ids = real.parse_external_ids
    helper.parse_collection = real.parse_collection
    return helper


@pytest.fixture
def mock_db_pool():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    return pool


@pytest.mark.asyncio
async def test_get_movie_data_returns_all_fields(mock_tmdb, mock_db_pool):
    movie = Movie("tt0137523", mock_db_pool, tmdb_helper=mock_tmdb)
    data = await movie.get_movie_data()

    assert data is not None
    assert data["_full"] is True
    assert data["title"] == "Fight Club"
    assert data["year"] == "1999"
    assert data["imdb_id"] == "tt0137523"
    assert data["tmdb_id"] == 550

    # Existing fields
    assert "Drama" in data["genres"]
    assert data["directors"] == "David Fincher"
    assert data["tagline"] == "Mischief. Mayhem. Soap."
    assert data["age_rating"] == "R"
    assert data["runtime"] == "139 min"
    assert data["budget"] == "$63,000,000"
    assert data["revenue"] == "$101,200,000"
    assert data["status"] == "Released"

    # Cast
    assert len(data["cast"]) == 3
    assert data["cast"][0]["name"] == "Brad Pitt"

    # Trailer
    assert "youtube.com" in data["trailer"]

    # New enriched fields
    assert data["key_crew"]["composer"] == "The Dust Brothers"
    assert "Jim Uhls" in data["key_crew"]["writers"]
    assert data["key_crew"]["cinematographer"] == "Jeff Cronenweth"

    assert data["keywords"] == ["fight", "underground", "soap"]
    assert len(data["recommendations"]) == 2
    assert data["recommendations"][0]["title"] == "Pulp Fiction"

    assert "imdb_url" in data["external_ids"]
    assert data["collection"]["name"] == "Fight Club Collection"
    assert data["homepage"] == "https://example.com/fightclub"

    # Watch providers
    assert data["watch_providers"]["stream"][0]["provider_name"] == "Netflix"
    assert "ads" in data["watch_providers"]


@pytest.mark.asyncio
async def test_get_movie_data_uses_combined_fetch(mock_tmdb, mock_db_pool):
    """Verify get_movie_full is called once (not individual endpoints)."""
    movie = Movie("tt0137523", mock_db_pool, tmdb_helper=mock_tmdb)
    await movie.get_movie_data()

    mock_tmdb.get_movie_full.assert_awaited_once_with(550)
    mock_tmdb.get_tmdb_id_by_tconst.assert_awaited_once_with("tt0137523")


@pytest.mark.asyncio
async def test_get_movie_data_returns_none_on_no_tmdb_id(mock_tmdb, mock_db_pool):
    mock_tmdb.get_tmdb_id_by_tconst = AsyncMock(return_value=None)
    movie = Movie("tt0000000", mock_db_pool, tmdb_helper=mock_tmdb)
    data = await movie.get_movie_data()
    assert data is None


@pytest.mark.asyncio
async def test_get_movie_data_handles_tmdb_failure(mock_tmdb, mock_db_pool):
    mock_tmdb.get_movie_full = AsyncMock(side_effect=Exception("API down"))
    movie = Movie("tt0137523", mock_db_pool, tmdb_helper=mock_tmdb)
    data = await movie.get_movie_data()
    # Should not crash — returns None or partial data
    # The gather catches exceptions, so it returns empty dict-based data
    assert data is None or isinstance(data, dict)


@pytest.mark.asyncio
async def test_get_movie_data_falls_back_to_tmdb_rating(mock_tmdb, mock_db_pool):
    """When DB ratings aren't available, fall back to TMDb vote_average."""
    mock_db_pool.execute = AsyncMock(return_value=None)
    movie = Movie("tt0137523", mock_db_pool, tmdb_helper=mock_tmdb)
    data = await movie.get_movie_data()

    assert data["rating"] == 8.4
    assert data["votes"] == 25000
