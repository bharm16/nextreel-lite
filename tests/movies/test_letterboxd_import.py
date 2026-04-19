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


from unittest.mock import AsyncMock

from movies.letterboxd_import import match_films, MatchResult


class TestMatchFilms:
    async def test_exact_match(self, mock_db_pool):
        mock_db_pool.execute.return_value = [
            {"tconst": "tt0137523", "primaryTitle": "Fight Club", "startYear": 1999},
        ]
        films = [{"name": "Fight Club", "year": 1999}]

        result = await match_films(mock_db_pool, films)

        assert len(result.matched) == 1
        assert result.matched[0] == "tt0137523"
        assert result.unmatched == []
        assert result.total == 1

    async def test_normalized_match(self, mock_db_pool):
        """Film with en-dash in Letterboxd matches hyphen in DB."""
        mock_db_pool.execute.return_value = [
            {"tconst": "tt0120915", "primaryTitle": "Star Wars: Episode I - The Phantom Menace", "startYear": 1999},
        ]
        films = [{"name": "Star Wars: Episode I \u2013 The Phantom Menace", "year": 1999}]

        result = await match_films(mock_db_pool, films)

        assert len(result.matched) == 1
        assert result.matched[0] == "tt0120915"

    async def test_unmatched_films(self, mock_db_pool):
        mock_db_pool.execute.return_value = []
        films = [{"name": "Nonexistent Movie", "year": 2099}]

        result = await match_films(mock_db_pool, films)

        assert result.matched == []
        assert len(result.unmatched) == 1
        assert result.unmatched[0] == {"name": "Nonexistent Movie", "year": 2099}

    async def test_mixed_matched_and_unmatched(self, mock_db_pool):
        mock_db_pool.execute.return_value = [
            {"tconst": "tt0137523", "primaryTitle": "Fight Club", "startYear": 1999},
        ]
        films = [
            {"name": "Fight Club", "year": 1999},
            {"name": "Unknown Film", "year": 2050},
        ]

        result = await match_films(mock_db_pool, films)

        assert len(result.matched) == 1
        assert len(result.unmatched) == 1
        assert result.total == 2

    async def test_empty_input(self, mock_db_pool):
        result = await match_films(mock_db_pool, [])

        assert result.matched == []
        assert result.unmatched == []
        assert result.total == 0
        mock_db_pool.execute.assert_not_awaited()

    async def test_query_uses_parameterized_placeholders(self, mock_db_pool):
        mock_db_pool.execute.return_value = []
        films = [{"name": "Inception", "year": 2010}]

        await match_films(mock_db_pool, films)

        # Pass 1: exact-match query. Must be parameterized and must NOT
        # wrap primaryTitle in LOWER/REPLACE — doing so defeated the
        # primaryTitle prefix index on movie_candidates.
        first_call = mock_db_pool.execute.await_args_list[0]
        query = first_call.args[0]
        assert "%s" in query
        assert "LOWER" not in query
        assert "REPLACE" not in query
        assert "primaryTitle IN" in query
        assert "startYear IN" in query


from movies.letterboxd_import import enqueue_import_enrichment


class TestEnqueueImportEnrichment:
    async def test_enqueues_jobs_for_non_ready_tconsts(self, mock_db_pool):
        """Only tconsts without READY projections get enqueued."""
        # Simulate: tt0000001 is READY, tt0000002 has no projection
        mock_db_pool.execute.return_value = [{"tconst": "tt0000001"}]

        enqueue_fn = AsyncMock()
        await enqueue_import_enrichment(
            ["tt0000001", "tt0000002"], mock_db_pool, enqueue_fn
        )

        # Only tt0000002 should be enqueued (tt0000001 is already READY)
        enqueue_fn.assert_awaited_once()
        call_args = enqueue_fn.call_args
        assert call_args[0][0] == "enrich_projection"
        assert call_args[0][1] == "tt0000002"

    async def test_empty_tconsts_does_nothing(self, mock_db_pool):
        enqueue_fn = AsyncMock()
        await enqueue_import_enrichment([], mock_db_pool, enqueue_fn)
        enqueue_fn.assert_not_awaited()
        mock_db_pool.execute.assert_not_awaited()

    async def test_all_already_ready(self, mock_db_pool):
        """If all tconsts are READY, no jobs enqueued."""
        mock_db_pool.execute.return_value = [
            {"tconst": "tt0000001"},
            {"tconst": "tt0000002"},
        ]
        enqueue_fn = AsyncMock()
        await enqueue_import_enrichment(
            ["tt0000001", "tt0000002"], mock_db_pool, enqueue_fn
        )
        enqueue_fn.assert_not_awaited()

    async def test_enqueue_failure_does_not_raise(self, mock_db_pool):
        """Enqueue errors are caught, not propagated."""
        mock_db_pool.execute.return_value = []
        enqueue_fn = AsyncMock(side_effect=Exception("arq down"))
        # Should not raise
        await enqueue_import_enrichment(
            ["tt0000001"], mock_db_pool, enqueue_fn
        )

    async def test_none_enqueue_fn_skips_silently(self, mock_db_pool):
        """If enqueue_fn is None, skip without error."""
        mock_db_pool.execute.return_value = []
        await enqueue_import_enrichment(
            ["tt0000001"], mock_db_pool, None
        )
