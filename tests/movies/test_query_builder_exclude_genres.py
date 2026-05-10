"""Tests for MovieQueryBuilder genre exclusion clauses.

The chip-based filter UI in ``templates/_filter_form.html`` lets users mark
genres as included; the bug at issue is that there was no way to mark a
genre as *excluded*. These tests pin the SQL the builder must produce when
``criteria["exclude_genres"]`` is non-empty.
"""

from __future__ import annotations

from movies.query_builder import MovieQueryBuilder


class TestExcludeGenreClauseFulltext:
    def test_returns_empty_when_no_exclude_genres(self):
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {}, use_fulltext=True, use_cache=True
        )
        assert clause == ""
        assert params == []

    def test_returns_empty_when_exclude_genres_is_empty_list(self):
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": []}, use_fulltext=True, use_cache=True
        )
        assert clause == ""
        assert params == []

    def test_single_excluded_genre_produces_negated_match_clause(self):
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": ["Drama"]}, use_fulltext=True, use_cache=True
        )
        assert "NOT MATCH" in clause
        assert "AGAINST(%s IN BOOLEAN MODE)" in clause
        # use_cache=True → no table alias prefix
        assert "tb.genres" not in clause
        assert "MATCH(genres)" in clause
        assert params == ['+"Drama"']

    def test_multiple_excluded_genres_produce_or_semantics_via_no_plus(self):
        """Multiple excluded genres should match if ANY are present.

        FULLTEXT BOOLEAN MODE: ``"Drama" "War"`` (no ``+``) means OR. We negate
        with NOT MATCH so the row is rejected when ANY excluded genre matches.
        """
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": ["Drama", "War"]}, use_fulltext=True, use_cache=True
        )
        assert "NOT MATCH" in clause
        # OR semantics: bare quoted terms, no leading "+".
        assert params == ['"Drama" "War"']

    def test_uses_table_alias_when_use_cache_false(self):
        clause, _ = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": ["Drama"]}, use_fulltext=True, use_cache=False
        )
        assert "MATCH(tb.genres)" in clause

    def test_strips_boolean_mode_operators_from_genre_names(self):
        """A crafted genre name must not let an attacker break out of the quoted term."""
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": ['Drama"+@<>~*()']}, use_fulltext=True, use_cache=True
        )
        # Operators stripped, only the bare token survives inside quotes.
        assert params == ['+"Drama"']
        assert "NOT MATCH" in clause


class TestExcludeGenreClauseLikeFallback:
    def test_returns_empty_when_no_exclude_genres(self):
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {}, use_fulltext=False, use_cache=True
        )
        assert clause == ""
        assert params == []

    def test_single_excluded_genre_produces_not_like(self):
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": ["Drama"]}, use_fulltext=False, use_cache=True
        )
        # use_cache=True → no alias.
        assert "genres NOT LIKE %s" in clause
        assert "tb.genres" not in clause
        assert params == ["%Drama%"]

    def test_multiple_excluded_genres_combined_with_and(self):
        """Excluding [Drama, War] means: NOT LIKE Drama AND NOT LIKE War."""
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": ["Drama", "War"]}, use_fulltext=False, use_cache=True
        )
        # Both terms must be absent — AND combination.
        assert clause.count("genres NOT LIKE %s") == 2
        assert " AND " in clause
        assert params == ["%Drama%", "%War%"]

    def test_uses_alias_when_use_cache_false(self):
        clause, _ = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": ["Drama"]}, use_fulltext=False, use_cache=False
        )
        assert "tb.genres NOT LIKE %s" in clause

    def test_escapes_like_wildcards_in_genre_name(self):
        clause, params = MovieQueryBuilder.exclude_genre_clause(
            {"exclude_genres": ["100%_Drama"]}, use_fulltext=False, use_cache=True
        )
        # % and _ must be backslash-escaped so they aren't treated as wildcards.
        assert params == [r"%100\%\_Drama%"]
        assert "genres NOT LIKE %s" in clause
