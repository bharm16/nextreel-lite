"""Tests for movies.letterboxd_import — Letterboxd CSV import logic."""

from __future__ import annotations

import io
import pytest

from movies.letterboxd_import import normalize_title, parse_watched_csv


class TestNormalizeTitle:
    def test_lowercase(self):
        assert normalize_title("GoodFellas") == "goodfellas"

    def test_en_dash_to_hyphen(self):
        assert normalize_title("Episode I \u2013 The Phantom Menace") == "episode i - the phantom menace"

    def test_em_dash_to_hyphen(self):
        assert normalize_title("Something \u2014 Else") == "something - else"

    def test_collapse_whitespace(self):
        assert normalize_title("The   Grand   Budapest") == "the grand budapest"

    def test_preserves_meaningful_punctuation(self):
        assert normalize_title("(500) Days of Summer") == "(500) days of summer"

    def test_preserves_colons(self):
        assert normalize_title("Star Wars: Episode I") == "star wars: episode i"

    def test_empty_string(self):
        assert normalize_title("") == ""


class TestParseWatchedCsv:
    def test_valid_csv(self):
        csv_text = "Date,Name,Year,Letterboxd URI\n2021-01-20,Inception,2010,https://boxd.it/1skk\n"
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert result == [{"name": "Inception", "year": 2010}]

    def test_multiple_rows(self):
        csv_text = (
            "Date,Name,Year,Letterboxd URI\n"
            "2021-01-20,Inception,2010,https://boxd.it/1skk\n"
            "2021-01-20,Tenet,2020,https://boxd.it/leq4\n"
        )
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert len(result) == 2
        assert result[0]["name"] == "Inception"
        assert result[1]["name"] == "Tenet"

    def test_missing_name_column_raises(self):
        csv_text = "Date,Title,Year,URI\n2021-01-20,Inception,2010,x\n"
        with pytest.raises(ValueError, match="Name"):
            parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))

    def test_missing_year_column_raises(self):
        csv_text = "Date,Name,Released,URI\n2021-01-20,Inception,2010,x\n"
        with pytest.raises(ValueError, match="Year"):
            parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))

    def test_skips_rows_with_non_integer_year(self):
        csv_text = "Date,Name,Year,Letterboxd URI\n2021-01-20,Inception,abc,x\n2021-01-20,Tenet,2020,x\n"
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert len(result) == 1
        assert result[0]["name"] == "Tenet"

    def test_skips_rows_with_empty_name(self):
        csv_text = "Date,Name,Year,Letterboxd URI\n2021-01-20,,2010,x\n2021-01-20,Tenet,2020,x\n"
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert len(result) == 1

    def test_empty_csv_body(self):
        csv_text = "Date,Name,Year,Letterboxd URI\n"
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert result == []
