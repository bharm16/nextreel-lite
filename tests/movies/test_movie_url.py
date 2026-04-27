"""Unit tests for movies.movie_url — pure path/slug builders."""

from __future__ import annotations

import pytest

from movies.movie_url import (
    build_movie_path,
    parse_movie_path,
    title_slug,
)


class TestTitleSlug:
    def test_basic_title_with_year(self):
        assert title_slug("The Departed", 2006) == "the-departed-2006"

    def test_strips_diacritics(self):
        assert title_slug("Amélie", 2001) == "amelie-2001"
        assert title_slug("Pokémon", 1998) == "pokemon-1998"

    def test_collapses_special_characters(self):
        assert (
            title_slug("Star Wars: Episode IV — A New Hope", 1977)
            == "star-wars-episode-iv-a-new-hope-1977"
        )

    def test_keeps_digits_in_title(self):
        assert title_slug("3:10 to Yuma", 2007) == "3-10-to-yuma-2007"
        assert title_slug("2001: A Space Odyssey", 1968) == "2001-a-space-odyssey-1968"

    def test_empty_title_falls_back_to_untitled(self):
        assert title_slug("", 2006) == "untitled-2006"
        assert title_slug(None, 2006) == "untitled-2006"

    def test_year_omitted_when_missing(self):
        assert title_slug("The Departed", None) == "the-departed"
        assert title_slug("The Departed", "") == "the-departed"
        assert title_slug("The Departed", "Unknown") == "the-departed"

    def test_year_accepts_string_or_int(self):
        assert title_slug("X", 2006) == "x-2006"
        assert title_slug("X", "2006") == "x-2006"

    def test_truncates_long_titles_at_80_chars_no_trailing_hyphen(self):
        long_title = "A" * 200
        result = title_slug(long_title, 2006)
        # Body capped at 80 chars; year appended after.
        assert result == ("a" * 80) + "-2006"
        assert "--" not in result
        assert not result.endswith("-")

    def test_truncation_does_not_leave_trailing_hyphen(self):
        # Title where the 80-char cut would land mid-separator.
        title = ("ab" * 39) + " word"  # 78 chars + " word"
        result = title_slug(title, 2006)
        assert "--" not in result
        # The slug body before the year should not end with "-".
        body, year = result.rsplit("-", 1)
        assert not body.endswith("-")


class TestBuildMoviePath:
    def test_renders_canonical_path(self):
        assert (
            build_movie_path("The Departed", 2006, "a8fk3j")
            == "/movie/the-departed-2006-a8fk3j"
        )

    def test_handles_missing_year(self):
        assert build_movie_path("The Departed", None, "a8fk3j") == "/movie/the-departed-a8fk3j"


class TestParseMoviePath:
    def test_parses_canonical(self):
        assert parse_movie_path("the-departed-2006-a8fk3j") == (
            "the-departed-2006",
            "a8fk3j",
        )

    def test_parses_minimal_one_char_title(self):
        # "M-1931-aaaaaa" — slug body is "m-1931", id is "aaaaaa".
        assert parse_movie_path("m-1931-aaaaaa") == ("m-1931", "aaaaaa")

    def test_returns_none_for_id_only(self):
        # No leading title — must have at least one slug char before the ID.
        assert parse_movie_path("a8fk3j") is None

    def test_returns_none_for_garbage(self):
        assert parse_movie_path("nonsense") is None
        assert parse_movie_path("") is None
        assert parse_movie_path("a/b") is None
        assert parse_movie_path("ABC-a8fk3j") is None  # uppercase rejected
        assert parse_movie_path("x-A8FK3J") is None    # uppercase ID rejected

    def test_returns_none_when_id_segment_wrong_length(self):
        assert parse_movie_path("title-12345") is None  # only 5 chars
        assert parse_movie_path("title-1234567") is None  # 7 chars
