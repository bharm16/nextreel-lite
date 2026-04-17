"""Tests for MovieQueryBuilder.build_search_query — the new title search used by /api/search."""

from __future__ import annotations

import pytest

from movies.query_builder import MovieQueryBuilder


def test_build_search_query_rejects_empty_query():
    """An empty query string yields no rows without hitting the DB."""
    query_sql, params = MovieQueryBuilder.build_search_query("", limit=10)
    assert query_sql is None
    assert params is None


def test_build_search_query_rejects_single_char_query():
    """A 1-char query is below the minimum length threshold."""
    query_sql, params = MovieQueryBuilder.build_search_query("a", limit=10)
    assert query_sql is None
    assert params is None


def test_build_search_query_builds_parameterized_query():
    """A valid query produces parameterized SQL against movie_candidates with %s placeholders."""
    query_sql, params = MovieQueryBuilder.build_search_query("chungking", limit=10)

    assert query_sql is not None
    # Queries the denormalized movie_candidates cache (not movie_projection, which stores JSON)
    assert "movie_candidates" in query_sql
    assert "primaryTitle" in query_sql
    assert "startYear" in query_sql
    # Must use parameterized placeholders — never f-string interpolation for values
    assert "%s" in query_sql
    assert "chungking" not in query_sql.lower()  # value appears only in params
    # Must order by relevance rank then rating
    assert "ORDER BY" in query_sql.upper()
    assert "LIMIT %s" in query_sql

    # Params include the three LIKE patterns (exact/prefix/contains) plus the limit
    # params[0] is the exact-match param, params[1] is "prefix%", params[2] is "%contains%"
    assert params[0].lower() == "chungking"
    assert params[1] == "chungking%"
    assert params[2] == "%chungking%"
    assert params[-1] == 10  # LIMIT bound


def test_build_search_query_escapes_sql_wildcards():
    """Queries containing % or _ must be escaped so they're treated as literals."""
    query_sql, params = MovieQueryBuilder.build_search_query("50%", limit=10)

    assert query_sql is not None
    # Escaped wildcards — the % in the user query should be prefixed with |
    assert any("50|%" in p or "|%" in p for p in params if isinstance(p, str))


def test_build_search_query_respects_custom_limit():
    """Limit is passed through as the final parameter."""
    _, params = MovieQueryBuilder.build_search_query("drama", limit=5)
    assert params[-1] == 5


def test_build_search_query_handles_underscore_in_query():
    """Underscore (SQL single-char wildcard) must be escaped as a literal."""
    query_sql, params = MovieQueryBuilder.build_search_query("foo_bar", limit=10)
    assert query_sql is not None
    # The underscore should be escaped to |_ in all three variants
    assert any("foo|_bar" in p for p in params if isinstance(p, str))


def test_build_search_query_handles_backslash_in_query():
    """A literal backslash in the query should not produce broken SQL."""
    query_sql, params = MovieQueryBuilder.build_search_query("foo\\bar", limit=10)
    assert query_sql is not None
    # Backslash is NOT a meta-char under our '|' escape scheme — passes through literally
    # (we only escape '|', '%', '_')
    assert any("foo\\bar" in p for p in params if isinstance(p, str))


def test_build_search_query_handles_pipe_in_query():
    """The pipe char (our escape char) must itself be escaped to ||."""
    query_sql, params = MovieQueryBuilder.build_search_query("a|b", limit=10)
    assert query_sql is not None
    # '|' should be doubled to '||' to pass through as a literal |
    assert any("a||b" in p for p in params if isinstance(p, str))
