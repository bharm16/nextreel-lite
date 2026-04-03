"""Tests for MovieQueryBuilder — pure SQL generation logic."""

from datetime import datetime
from unittest.mock import patch

import pytest

from movies.query_builder import MovieQueryBuilder


class TestShouldUseCache:
    """MovieQueryBuilder.should_use_cache routing logic."""

    def test_cache_for_modern_high_vote_criteria(self):
        criteria = {"min_year": 1990, "max_year": 2025, "min_votes": 50000}
        assert MovieQueryBuilder.should_use_cache(criteria) is True

    def test_no_cache_for_old_movies(self):
        criteria = {"min_year": 1920, "max_year": 1970, "min_votes": 50000}
        assert MovieQueryBuilder.should_use_cache(criteria) is False

    def test_no_cache_for_low_votes(self):
        criteria = {"min_year": 2000, "max_year": 2025, "min_votes": 500}
        assert MovieQueryBuilder.should_use_cache(criteria) is False

    def test_defaults_when_keys_missing(self):
        # Default min_year=1900 < 1980 → should NOT use cache
        assert MovieQueryBuilder.should_use_cache({}) is False

    def test_boundary_at_1980(self):
        criteria = {"min_year": 1980, "max_year": 2025, "min_votes": 10000}
        assert MovieQueryBuilder.should_use_cache(criteria) is True

    def test_boundary_below_1980(self):
        criteria = {"min_year": 1979, "max_year": 2025, "min_votes": 10000}
        assert MovieQueryBuilder.should_use_cache(criteria) is False


class TestShouldUseRecentCache:
    """MovieQueryBuilder.should_use_recent_cache rolling threshold."""

    def test_recent_years_use_recent_cache(self):
        current_year = datetime.now().year
        criteria = {"min_year": current_year - 1, "max_year": current_year}
        assert MovieQueryBuilder.should_use_recent_cache(criteria) is True

    def test_old_years_skip_recent_cache(self):
        criteria = {"min_year": 1950, "max_year": 1980}
        assert MovieQueryBuilder.should_use_recent_cache(criteria) is False

    def test_broad_range_touching_recent_years_still_skips_recent_cache(self):
        threshold = datetime.now().year - 2
        criteria = {"min_year": 1900, "max_year": threshold}
        assert MovieQueryBuilder.should_use_recent_cache(criteria) is False

    def test_range_must_start_within_recent_window(self):
        threshold = datetime.now().year - 2
        criteria = {"min_year": threshold, "max_year": datetime.now().year}
        assert MovieQueryBuilder.should_use_recent_cache(criteria) is True

    def test_max_year_below_threshold(self):
        threshold = datetime.now().year - 2
        criteria = {"min_year": 1900, "max_year": threshold - 1}
        assert MovieQueryBuilder.should_use_recent_cache(criteria) is False


class TestBuildBaseQuery:
    """SQL template selection based on table routing flags."""

    def test_recent_cache_table(self):
        sql = MovieQueryBuilder.build_base_query(use_recent=True)
        assert "recent_movies_cache" in sql
        assert "FORCE INDEX" not in sql

    def test_popular_cache_table(self):
        sql = MovieQueryBuilder.build_base_query(use_cache=True)
        assert "popular_movies_cache" in sql
        assert "FORCE INDEX" not in sql

    def test_main_table_without_force_index(self):
        sql = MovieQueryBuilder.build_base_query()
        assert "title.basics" in sql
        assert "FORCE INDEX" not in sql
        assert "title.ratings" in sql

    def test_recent_takes_precedence_over_cache(self):
        # When both flags are set, use_recent should produce recent_movies_cache
        sql = MovieQueryBuilder.build_base_query(use_cache=True, use_recent=True)
        assert "recent_movies_cache" in sql

    def test_explicit_column_list_not_star(self):
        """All paths should use explicit columns, not SELECT *."""
        for kwargs in [
            {},
            {"use_cache": True},
            {"use_recent": True},
        ]:
            sql = MovieQueryBuilder.build_base_query(**kwargs)
            assert "SELECT *" not in sql
            assert "tconst" in sql

    def test_all_paths_share_same_where_structure(self):
        """All three query paths produce the same WHERE placeholder count."""
        for kwargs in [
            {},
            {"use_cache": True},
            {"use_recent": True},
        ]:
            sql = MovieQueryBuilder.build_base_query(**kwargs)
            assert sql.count("%s") == 10


class TestBuildParameters:
    """Parameter list generation for prepared statements — unified order."""

    def test_unified_param_order(self):
        criteria = {
            "title_type": "movie",
            "min_year": 2000,
            "max_year": 2025,
            "min_votes": 10000,
            "max_votes": 500000,
            "min_rating": 6.0,
            "max_rating": 9.0,
            "language": "en",
        }
        params = MovieQueryBuilder.build_parameters(criteria)
        # Unified order: title_type, min_year, max_year, min_rating,
        # max_rating, min_votes, max_votes, language_check, language_exact,
        # language_pattern
        assert params[0] == "movie"
        assert params[1] == 2000
        assert params[2] == 2025
        assert params[3] == 6.0
        assert params[4] == 9.0
        assert params[5] == 10000
        assert params[6] == 500000
        assert len(params) == 10

    def test_language_any_produces_wildcard(self):
        criteria = {"language": "any"}
        params = MovieQueryBuilder.build_parameters(criteria)
        # language_check should be "any", exact "any", pattern "%"
        assert params[-3] == "any"
        assert params[-2] == "any"
        assert params[-1] == "%"

    def test_language_en_produces_like_pattern(self):
        criteria = {"language": "en"}
        params = MovieQueryBuilder.build_parameters(criteria)
        assert params[-3] == "en"
        assert params[-2] == "en"
        assert params[-1] == "%en%"

    def test_defaults_fill_in_for_empty_criteria(self):
        params = MovieQueryBuilder.build_parameters({})
        assert len(params) == 10
        assert params[0] == "movie"   # default title_type
        assert params[1] == 1900      # default min_year
        assert params[3] == 7.0       # default min_rating


class TestBuildCountQuery:
    """COUNT(*) queries should share the same structure as base queries."""

    def test_count_query_same_placeholder_count(self):
        for kwargs in [
            {},
            {"use_cache": True},
            {"use_recent": True},
        ]:
            base = MovieQueryBuilder.build_base_query(**kwargs)
            count = MovieQueryBuilder.build_count_query(**kwargs)
            assert base.count("%s") == count.count("%s")

    def test_count_query_no_force_index(self):
        sql = MovieQueryBuilder.build_count_query()
        assert "FORCE INDEX" not in sql


class TestBuildGenreConditionsFulltext:
    """FULLTEXT genre condition builder, including sanitization."""

    def test_no_genres_returns_empty(self):
        cond, params = MovieQueryBuilder.build_genre_conditions_fulltext({})
        assert cond == ""
        assert params == []

    def test_single_genre(self):
        criteria = {"genres": ["Action"]}
        cond, params = MovieQueryBuilder.build_genre_conditions_fulltext(criteria)
        assert "MATCH" in cond
        assert "AGAINST" in cond
        assert "IN BOOLEAN MODE" in cond
        assert '+\"Action\"' in params[0]

    def test_multiple_genres_concatenated(self):
        criteria = {"genres": ["Action", "Comedy", "Drama"]}
        cond, params = MovieQueryBuilder.build_genre_conditions_fulltext(criteria)
        genre_search = params[0]
        assert '+\"Action\"' in genre_search
        assert '+\"Comedy\"' in genre_search
        assert '+\"Drama\"' in genre_search

    def test_15_plus_genres_skipped(self):
        criteria = {"genres": [f"Genre{i}" for i in range(15)]}
        cond, params = MovieQueryBuilder.build_genre_conditions_fulltext(criteria)
        assert cond == ""
        assert params == []

    def test_14_genres_not_skipped(self):
        criteria = {"genres": [f"Genre{i}" for i in range(14)]}
        cond, params = MovieQueryBuilder.build_genre_conditions_fulltext(criteria)
        assert cond != ""

    def test_cache_table_no_alias(self):
        criteria = {"genres": ["Action"]}
        cond, _ = MovieQueryBuilder.build_genre_conditions_fulltext(criteria, use_cache=True)
        assert "MATCH(genres)" in cond

    def test_non_cache_has_table_alias(self):
        criteria = {"genres": ["Action"]}
        cond, _ = MovieQueryBuilder.build_genre_conditions_fulltext(criteria, use_cache=False)
        assert "MATCH(tb.genres)" in cond

    def test_double_quotes_stripped_from_genre(self):
        """Genre names with double quotes should not break FULLTEXT boolean mode."""
        criteria = {"genres": ['Sci"Fi']}
        cond, params = MovieQueryBuilder.build_genre_conditions_fulltext(criteria)
        genre_search = params[0]
        # The double quote should be stripped
        assert '"' not in genre_search.replace('+\"', '').replace('\"', '')
        # But the genre name content should be preserved (minus the quote)
        assert "SciFi" in genre_search

    def test_boolean_operators_stripped(self):
        """FULLTEXT boolean operators (+, -, *, ~, etc.) in genre names are stripped."""
        criteria = {"genres": ["Action+Drama", "Sci-Fi", "Comedy*"]}
        cond, params = MovieQueryBuilder.build_genre_conditions_fulltext(criteria)
        genre_search = params[0]
        # + between Action and Drama should be removed
        assert "ActionDrama" in genre_search
        # - between Sci and Fi should be removed
        assert "SciFi" in genre_search

    def test_at_sign_stripped(self):
        criteria = {"genres": ["Test@Genre"]}
        _, params = MovieQueryBuilder.build_genre_conditions_fulltext(criteria)
        assert "@" not in params[0]


class TestBuildGenreConditionsLike:
    """Fallback LIKE-based genre conditions."""

    def test_no_genres_empty(self):
        params = []
        result = MovieQueryBuilder.build_genre_conditions({}, params)
        assert result == []
        assert params == []

    def test_genres_produce_like_clauses(self):
        params = []
        criteria = {"genres": ["Action", "Comedy"]}
        result = MovieQueryBuilder.build_genre_conditions(criteria, params)
        assert len(result) == 1
        assert "LIKE %s" in result[0]
        assert len(params) == 2
        assert params[0] == "%Action%"
        assert params[1] == "%Comedy%"

    def test_cache_table_no_alias(self):
        params = []
        criteria = {"genres": ["Drama"]}
        result = MovieQueryBuilder.build_genre_conditions(criteria, params, use_cache=True)
        assert "genres LIKE" in result[0]
        assert "tb." not in result[0]

    def test_non_cache_has_table_alias(self):
        params = []
        criteria = {"genres": ["Drama"]}
        result = MovieQueryBuilder.build_genre_conditions(criteria, params, use_cache=False)
        assert "tb.genres LIKE" in result[0]

    def test_15_plus_genres_skipped(self):
        params = []
        criteria = {"genres": [f"G{i}" for i in range(15)]}
        result = MovieQueryBuilder.build_genre_conditions(criteria, params)
        assert result == []
        assert params == []
