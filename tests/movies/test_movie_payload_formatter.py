"""Unit tests for movie payload formatting."""

from __future__ import annotations

from movies.tmdb_client import TMDbHelper
from tests.movies.test_tmdb_parsers import _sample_combined_response


def test_assemble_preserves_existing_movie_payload_shape():
    from movies.movie_payload import MoviePayloadFormatter

    helper = TMDbHelper("key")
    full_data = _sample_combined_response()

    payload = MoviePayloadFormatter().assemble(
        full_data=full_data,
        ratings_data={"averageRating": 8.8, "numVotes": 123456},
        tmdb_helper=helper,
        tconst="tt0137523",
        slug="fight-club",
        tmdb_id=550,
    )

    assert payload["title"] == "Fight Club"
    assert payload["tconst"] == "tt0137523"
    assert payload["imdb_id"] == "tt0137523"
    assert payload["tmdb_id"] == 550
    assert payload["slug"] == "fight-club"
    assert payload["rating"] == 8.8
    assert payload["votes"] == 123456
    assert payload["poster_url"].endswith("/w500/poster.jpg")
    assert payload["year"] == "1999"
    assert payload["budget"] == "$63,000,000"
    assert payload["revenue"] == "$101,200,000"
    assert payload["runtime"] == "139 min"
    assert payload["production_countries"] == "United States of America"
    assert payload["_full"] is True


def test_assemble_falls_back_to_tmdb_votes_and_unknown_labels():
    from movies.movie_payload import MoviePayloadFormatter

    helper = TMDbHelper("key")
    full_data = _sample_combined_response()
    full_data["budget"] = 0
    full_data["revenue"] = 0
    full_data["runtime"] = 0
    full_data["production_countries"] = []

    payload = MoviePayloadFormatter().assemble(
        full_data=full_data,
        ratings_data=None,
        tmdb_helper=helper,
        tconst="tt0137523",
        slug=None,
        tmdb_id=550,
    )

    assert payload["rating"] == full_data["vote_average"]
    assert payload["votes"] == full_data["vote_count"]
    assert payload["budget"] == "Unknown"
    assert payload["revenue"] == "Unknown"
    assert payload["runtime"] == "Unknown"
    assert payload["production_countries"] == "Unknown"
