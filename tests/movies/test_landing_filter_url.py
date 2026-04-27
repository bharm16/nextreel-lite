"""Tests for movies.landing_filter_url translation helpers."""

from __future__ import annotations

from movies.landing_filter_url import (
    active_filters_for_template,
    criteria_from_query_args,
)


def test_criteria_from_empty_args_returns_empty():
    assert criteria_from_query_args({}) == {}


def test_criteria_genre_drama():
    assert criteria_from_query_args({"genre": "Drama"}) == {"genres": ["Drama"]}


def test_criteria_genre_invalid_dropped():
    assert criteria_from_query_args({"genre": "NotARealGenre"}) == {}


def test_criteria_genre_case_sensitive():
    """VALID_GENRES is case-sensitive — lowercase is dropped."""
    assert criteria_from_query_args({"genre": "drama"}) == {}


def test_criteria_decade_1990s():
    result = criteria_from_query_args({"decade": "1990s"})
    assert result == {"min_year": 1990, "max_year": 1999}


def test_criteria_decade_invalid_dropped():
    assert criteria_from_query_args({"decade": "1990"}) == {}
    assert criteria_from_query_args({"decade": "abc"}) == {}


def test_criteria_runtime_lt120():
    assert criteria_from_query_args({"runtime": "lt120"}) == {"max_runtime": 120}


def test_criteria_runtime_lt90():
    assert criteria_from_query_args({"runtime": "lt90"}) == {"max_runtime": 90}


def test_criteria_runtime_gt150():
    assert criteria_from_query_args({"runtime": "gt150"}) == {"min_runtime": 150}


def test_criteria_runtime_invalid_dropped():
    assert criteria_from_query_args({"runtime": "lt60"}) == {}


def test_criteria_rating_7plus():
    assert criteria_from_query_args({"rating": "7plus"}) == {"min_rating": 7.0}


def test_criteria_rating_6plus_and_8plus():
    assert criteria_from_query_args({"rating": "6plus"}) == {"min_rating": 6.0}
    assert criteria_from_query_args({"rating": "8plus"}) == {"min_rating": 8.0}


def test_criteria_rating_invalid_dropped():
    assert criteria_from_query_args({"rating": "5plus"}) == {}


def test_criteria_combined():
    result = criteria_from_query_args(
        {
            "genre": "Drama",
            "decade": "1990s",
            "runtime": "lt120",
            "rating": "7plus",
        }
    )
    assert result == {
        "genres": ["Drama"],
        "min_year": 1990,
        "max_year": 1999,
        "max_runtime": 120,
        "min_rating": 7.0,
    }


def test_criteria_unknown_param_ignored():
    assert criteria_from_query_args({"foo": "bar", "genre": "Drama"}) == {
        "genres": ["Drama"]
    }


def test_active_filters_for_template_empty():
    assert active_filters_for_template({}) == {}


def test_active_filters_for_template_genre_only():
    """Translates URL-state criteria back to /filtered_movie form-schema keys."""
    result = active_filters_for_template({"genres": ["Drama"]})
    assert result == {"genres[]": "Drama"}


def test_active_filters_for_template_decade():
    result = active_filters_for_template({"min_year": 1990, "max_year": 1999})
    assert result == {"year_min": "1990", "year_max": "1999"}


def test_active_filters_for_template_runtime_is_dropped():
    """/filtered_movie (normalize_filters) has no runtime form key — drop silently."""
    assert active_filters_for_template({"max_runtime": 120}) == {}
    assert active_filters_for_template({"min_runtime": 150}) == {}


def test_active_filters_for_template_rating():
    assert active_filters_for_template({"min_rating": 7.0}) == {"imdb_score_min": "7.0"}


def test_active_filters_for_template_multi_genre_keeps_only_first():
    """Only the first genre is forwarded — the landing strip is single-genre."""
    result = active_filters_for_template({"genres": ["Drama", "Comedy"]})
    assert result == {"genres[]": "Drama"}


def test_active_filters_for_template_combined():
    """Runtime is absent from the output because normalize_filters has no runtime key."""
    criteria = {
        "genres": ["Drama"],
        "min_year": 1990,
        "max_year": 1999,
        "max_runtime": 120,
        "min_rating": 7.0,
    }
    result = active_filters_for_template(criteria)
    assert result == {
        "genres[]": "Drama",
        "year_min": "1990",
        "year_max": "1999",
        "imdb_score_min": "7.0",
    }
