"""Tests for the extracted filter normalizer."""

from infra.filter_normalizer import (
    criteria_from_filters,
    default_filter_state,
    filters_from_criteria,
    normalize_filters,
    validate_filters,
)


def test_default_filter_state_has_expected_keys():
    state = default_filter_state()
    assert "year_min" in state
    assert "year_max" in state
    assert "language" in state
    assert state["genres_selected"] == []


def test_default_filter_state_respects_year():
    state = default_filter_state(current_year=2025)
    assert state["year_max"] == 2025


def test_filters_from_criteria_round_trip():
    criteria = {
        "min_year": 1990,
        "max_year": 2020,
        "min_rating": 6.0,
        "language": "fr",
        "genres": ["Drama"],
    }
    filters = filters_from_criteria(criteria)
    assert filters["year_min"] == 1990
    assert filters["year_max"] == 2020
    assert filters["imdb_score_min"] == 6.0
    assert filters["language"] == "fr"
    assert filters["genres_selected"] == ["Drama"]


def test_normalize_filters_from_form():
    class FakeForm:
        def get(self, key, default=None):
            return {"year_min": "2000", "language": "es"}.get(key, default)

        def getlist(self, key):
            if key == "genres[]":
                return ["Action", "Comedy"]
            return []

    result = normalize_filters(FakeForm())
    assert result["year_min"] == "2000"
    assert result["language"] == "es"
    assert result["genres_selected"] == ["Action", "Comedy"]


def test_normalize_filters_rejects_invalid_genres():
    class FakeForm:
        def get(self, key, default=None):
            return default

        def getlist(self, key):
            if key == "genres[]":
                return ["Action", "NotAGenre", "Drama"]
            return []

    result = normalize_filters(FakeForm())
    assert "NotAGenre" not in result["genres_selected"]
    assert "Action" in result["genres_selected"]
    assert "Drama" in result["genres_selected"]


def test_criteria_from_filters_produces_query_criteria():
    filters = default_filter_state()
    criteria = criteria_from_filters(filters)
    assert "min_year" in criteria or "max_year" in criteria


def test_validate_filters_rejects_reversed_year_range():
    errors = validate_filters(
        {
            "year_min": "2025",
            "year_max": "1990",
            "imdb_score_min": "7.0",
            "imdb_score_max": "8.0",
            "num_votes_min": "100",
            "num_votes_max": "1000",
        }
    )

    assert "year_min" in errors
    assert "year_max" in errors


def test_validate_filters_rejects_reversed_rating_range():
    errors = validate_filters(
        {
            "year_min": "1990",
            "year_max": "2025",
            "imdb_score_min": "9.5",
            "imdb_score_max": "8.0",
            "num_votes_min": "100",
            "num_votes_max": "1000",
        }
    )

    assert "imdb_score_min" in errors
    assert "imdb_score_max" in errors


def test_validate_filters_rejects_reversed_vote_range():
    errors = validate_filters(
        {
            "year_min": "1990",
            "year_max": "2025",
            "imdb_score_min": "7.0",
            "imdb_score_max": "8.0",
            "num_votes_min": "5000",
            "num_votes_max": "1000",
        }
    )

    assert "num_votes_min" in errors
    assert "num_votes_max" in errors


def test_validate_filters_rejects_malformed_numeric_values():
    errors = validate_filters(
        {
            "year_min": "nineteen-ninety",
            "year_max": "2201",
            "imdb_score_min": "high",
            "imdb_score_max": "11.0",
            "num_votes_min": "lots",
            "num_votes_max": "1000",
        }
    )

    assert "year_min" in errors
    assert "year_max" in errors
    assert "imdb_score_min" in errors
    assert "imdb_score_max" in errors
    assert "num_votes_min" in errors


def test_validate_filters_allows_empty_genres_as_all_genres():
    errors = validate_filters(
        {
            "year_min": "1990",
            "year_max": "2025",
            "imdb_score_min": "7.0",
            "imdb_score_max": "9.0",
            "num_votes_min": "1000",
            "num_votes_max": "5000",
            "genres_selected": [],
        }
    )

    assert errors == {}
