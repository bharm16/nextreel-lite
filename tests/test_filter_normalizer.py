"""Tests for the extracted filter normalizer."""

from infra.filter_normalizer import (
    criteria_from_filters,
    default_filter_state,
    filters_from_criteria,
    normalize_filters,
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
