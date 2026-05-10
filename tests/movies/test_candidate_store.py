"""Tests for movies.candidate_store — CandidateStore data-access layer."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from infra.errors import DatabaseError
from infra.time_utils import utcnow
from movies.candidate_store import (
    SAMPLE_BUCKET_COUNT,
    SELECTION_BUCKET_STEPS,
    CandidateStore,
    _ALLOWED_CANDIDATE_TABLES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(mock_db_pool) -> CandidateStore:
    return CandidateStore(mock_db_pool)


def _row(tconst: str, title: str = "Test Movie", slug: str | None = "test-movie") -> dict:
    return {"tconst": tconst, "primaryTitle": title, "slug": slug}


# ---------------------------------------------------------------------------
# latest_refresh_at
# ---------------------------------------------------------------------------


async def test_latest_refresh_at_returns_datetime(mock_db_pool):
    """Returns the refreshed_at datetime from the DB row."""
    now = utcnow()
    mock_db_pool.execute.return_value = {"refreshed_at": now}
    store = _make_store(mock_db_pool)

    result = await store.latest_refresh_at()

    assert result == now
    mock_db_pool.execute.assert_awaited_once()
    call_args = mock_db_pool.execute.call_args
    assert "ORDER BY refreshed_at DESC" in call_args[0][0]
    assert call_args[1]["fetch"] == "one"


async def test_latest_refresh_at_returns_none_when_no_rows(mock_db_pool):
    """Returns None when the query returns no row."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    result = await store.latest_refresh_at()

    assert result is None


async def test_latest_refresh_at_returns_none_when_refreshed_at_is_null(mock_db_pool):
    """Returns None when the row exists but refreshed_at is NULL (empty table)."""
    mock_db_pool.execute.return_value = {"refreshed_at": None}
    store = _make_store(mock_db_pool)

    result = await store.latest_refresh_at()

    assert result is None


# ---------------------------------------------------------------------------
# has_fresh_data
# ---------------------------------------------------------------------------


async def test_has_fresh_data_true_when_within_max_age(mock_db_pool):
    """Returns True when data is younger than max_age_hours."""
    recent = utcnow() - timedelta(hours=1)
    mock_db_pool.execute.return_value = {"refreshed_at": recent}
    store = _make_store(mock_db_pool)

    assert await store.has_fresh_data(max_age_hours=24) is True


async def test_has_fresh_data_false_when_stale(mock_db_pool):
    """Returns False when data is older than max_age_hours."""
    old = utcnow() - timedelta(hours=48)
    mock_db_pool.execute.return_value = {"refreshed_at": old}
    store = _make_store(mock_db_pool)

    assert await store.has_fresh_data(max_age_hours=24) is False


async def test_has_fresh_data_false_when_no_data(mock_db_pool):
    """Returns False when latest_refresh_at returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    assert await store.has_fresh_data() is False


async def test_has_fresh_data_boundary_near_age(mock_db_pool):
    """Returns True when data age is just under max_age_hours."""
    almost = utcnow() - timedelta(hours=23, minutes=59)
    mock_db_pool.execute.return_value = {"refreshed_at": almost}
    store = _make_store(mock_db_pool)

    assert await store.has_fresh_data(max_age_hours=24) is True


async def test_has_fresh_data_custom_max_age(mock_db_pool):
    """Respects a custom max_age_hours value."""
    two_hours_ago = utcnow() - timedelta(hours=2)
    mock_db_pool.execute.return_value = {"refreshed_at": two_hours_ago}
    store = _make_store(mock_db_pool)

    assert await store.has_fresh_data(max_age_hours=1) is False
    assert await store.has_fresh_data(max_age_hours=3) is True


# ---------------------------------------------------------------------------
# fetch_ref
# ---------------------------------------------------------------------------


async def test_fetch_ref_returns_normalized_dict(mock_db_pool):
    """Returns a dict with tconst, title, slug, public_id, year keys."""
    mock_db_pool.execute.return_value = {
        "tconst": "tt1234567",
        "primaryTitle": "Inception",
        "slug": "inception",
        "public_id": "abc123",
        "startYear": 2010,
    }
    store = _make_store(mock_db_pool)

    result = await store.fetch_ref("tt1234567")

    assert result == {
        "tconst": "tt1234567",
        "title": "Inception",
        "slug": "inception",
        "public_id": "abc123",
        "year": "2010",
    }


async def test_fetch_ref_returns_none_public_id_when_missing(mock_db_pool):
    """Pre-enrichment rows have NULL public_id; the ref carries it as None."""
    mock_db_pool.execute.return_value = {
        "tconst": "tt1234567",
        "primaryTitle": "Inception",
        "slug": "inception",
        "public_id": None,
    }
    store = _make_store(mock_db_pool)

    result = await store.fetch_ref("tt1234567")

    assert result["public_id"] is None


async def test_fetch_ref_returns_none_when_not_found(mock_db_pool):
    """Returns None when the tconst doesn't exist in either table."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    result = await store.fetch_ref("tt0000000")

    assert result is None


async def test_fetch_ref_defaults_title_to_unknown(mock_db_pool):
    """Uses 'Unknown' when primaryTitle is None."""
    mock_db_pool.execute.return_value = {
        "tconst": "tt9999999",
        "primaryTitle": None,
        "slug": None,
    }
    store = _make_store(mock_db_pool)

    result = await store.fetch_ref("tt9999999")

    assert result["title"] == "Unknown"
    assert result["slug"] is None


async def test_fetch_ref_passes_tconst_twice(mock_db_pool):
    """The UNION ALL query requires tconst passed as both parameters."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    await store.fetch_ref("tt1111111")

    call_args = mock_db_pool.execute.call_args
    params = call_args[0][1]
    assert params == ["tt1111111", "tt1111111"]


# ---------------------------------------------------------------------------
# fetch_refs (batched)
# ---------------------------------------------------------------------------


async def test_fetch_refs_empty_input_returns_empty_list(mock_db_pool):
    """No DB call when called with an empty list."""
    store = _make_store(mock_db_pool)
    result = await store.fetch_refs([])
    assert result == []
    mock_db_pool.execute.assert_not_called()


async def test_fetch_refs_returns_normalized_dicts(mock_db_pool):
    """Multi-tconst input → list of normalized refs."""
    mock_db_pool.execute.return_value = [
        {
            "tconst": "tt1",
            "primaryTitle": "One",
            "slug": "one",
            "public_id": "aaaaaa",
            "startYear": 2001,
        },
        {
            "tconst": "tt2",
            "primaryTitle": "Two",
            "slug": "two",
            "public_id": None,
            "startYear": None,
        },
    ]
    store = _make_store(mock_db_pool)
    result = await store.fetch_refs(["tt1", "tt2"])
    assert {r["tconst"] for r in result} == {"tt1", "tt2"}
    assert all(
        set(r.keys()) == {"tconst", "title", "slug", "public_id", "year"}
        for r in result
    )
    by_tconst = {r["tconst"]: r for r in result}
    assert by_tconst["tt1"]["public_id"] == "aaaaaa"
    assert by_tconst["tt1"]["year"] == "2001"
    assert by_tconst["tt2"]["public_id"] is None
    assert by_tconst["tt2"]["year"] is None


async def test_fetch_refs_skips_missing_and_dedupes(mock_db_pool):
    """Missing tconsts simply absent; duplicates collapse."""
    mock_db_pool.execute.return_value = [
        {"tconst": "tt1", "primaryTitle": "One", "slug": "one"},
        # tt2 missing — DB returned no row for it
        {"tconst": "tt1", "primaryTitle": "One", "slug": "one"},  # duplicate row
    ]
    store = _make_store(mock_db_pool)
    result = await store.fetch_refs(["tt1", "tt2", "tt1"])
    assert len(result) == 1
    assert result[0]["tconst"] == "tt1"


# ---------------------------------------------------------------------------
# _genre_clause
# ---------------------------------------------------------------------------


def test_genre_clause_empty_for_no_genres(mock_db_pool):
    """Returns empty clause and params when genres list is absent."""
    store = _make_store(mock_db_pool)
    clause, params = store._genre_clause({})

    assert clause == ""
    assert params == []


def test_genre_clause_empty_for_empty_genres_list(mock_db_pool):
    """Returns empty clause and params when genres list is empty."""
    store = _make_store(mock_db_pool)
    clause, params = store._genre_clause({"genres": []})

    assert clause == ""
    assert params == []


def test_genre_clause_returns_fulltext_for_genres(mock_db_pool):
    """Returns a FULLTEXT MATCH clause for a non-empty genre list."""
    store = _make_store(mock_db_pool)
    clause, params = store._genre_clause({"genres": ["Action", "Comedy"]})

    assert "MATCH(genres) AGAINST(%s IN BOOLEAN MODE)" in clause
    assert len(params) == 1
    assert '+"Action"' in params[0]
    assert '+"Comedy"' in params[0]


def test_genre_clause_skips_when_fifteen_or_more_genres(mock_db_pool):
    """Skips genre filtering when 15+ genres are selected (effectively 'all')."""
    store = _make_store(mock_db_pool)
    many_genres = [f"Genre{i}" for i in range(15)]
    clause, params = store._genre_clause({"genres": many_genres})

    assert clause == ""
    assert params == []


def test_genre_clause_strips_boolean_operators(mock_db_pool):
    """Boolean mode operators (+, -, <, >, ~, *, etc.) are stripped from genre names."""
    store = _make_store(mock_db_pool)
    clause, params = store._genre_clause({"genres": ["Sci-Fi+", '"Horror"', "Action*"]})

    assert len(params) == 1
    search_string = params[0]
    # The stripped characters should not appear unquoted in the genre names
    # The function wraps each in +"...", so the quotes in the search are from the function
    assert "SciFi" in search_string  # dash and plus stripped
    assert "Horror" in search_string  # original quotes stripped
    assert "Action" in search_string  # asterisk stripped


def test_genre_clause_single_genre(mock_db_pool):
    """Works correctly with a single genre."""
    store = _make_store(mock_db_pool)
    clause, params = store._genre_clause({"genres": ["Drama"]})

    assert "MATCH(genres)" in clause
    assert params == ['+"Drama"']


# ---------------------------------------------------------------------------
# _build_movie_filter_clauses — exclude_genres integration
# ---------------------------------------------------------------------------


def test_build_movie_filter_clauses_appends_exclude_genre_clause(mock_db_pool):
    """When criteria contains exclude_genres, the returned genre_clause
    must contain a NOT MATCH fragment so Drama-tagged rows are rejected.

    This is the bug the user reported: there was no way to exclude a
    genre from results. The clause+params returned here are concatenated
    onto the WHERE in ``_build_candidate_query`` and ``count_matching``.
    """
    store = _make_store(mock_db_pool)
    _, _, genre_clause, genre_params = store._build_movie_filter_clauses(
        {"exclude_genres": ["Drama"]}, use_fulltext=True
    )

    assert "NOT MATCH(genres)" in genre_clause
    assert '+"Drama"' in genre_params


def test_build_movie_filter_clauses_combines_include_and_exclude_genres(mock_db_pool):
    """Both clauses must coexist — include picks the bucket, exclude trims it."""
    store = _make_store(mock_db_pool)
    _, _, genre_clause, genre_params = store._build_movie_filter_clauses(
        {"genres": ["Action"], "exclude_genres": ["Drama"]}, use_fulltext=True
    )

    # Two separate MATCH expressions: one positive, one negated.
    assert genre_clause.count("MATCH(genres)") == 2
    assert "NOT MATCH(genres)" in genre_clause
    # Param order matters — placeholders are positional in the concatenated SQL.
    assert genre_params == ['+"Action"', '+"Drama"']


def test_build_movie_filter_clauses_exclude_genres_uses_like_fallback(mock_db_pool):
    """When use_fulltext=False, exclude clause uses NOT LIKE."""
    store = _make_store(mock_db_pool)
    _, _, genre_clause, genre_params = store._build_movie_filter_clauses(
        {"exclude_genres": ["Drama"]}, use_fulltext=False
    )

    assert "genres NOT LIKE %s" in genre_clause
    assert genre_params == ["%Drama%"]


def test_build_movie_filter_clauses_no_genre_clause_when_neither_set(mock_db_pool):
    """No exclude_genres and no genres → empty genre_clause (regression check)."""
    store = _make_store(mock_db_pool)
    _, _, genre_clause, genre_params = store._build_movie_filter_clauses(
        {}, use_fulltext=True
    )

    assert genre_clause == ""
    assert genre_params == []


# ---------------------------------------------------------------------------
# fetch_candidate_refs
# ---------------------------------------------------------------------------


async def test_fetch_candidate_refs_returns_refs(mock_db_pool):
    """Returns normalized ref dicts from DB rows.

    The method only returns when ``len(deduped) >= desired_limit``, so we
    must supply at least *limit* distinct rows for it to succeed.
    """
    mock_db_pool.execute.return_value = [
        _row("tt0001", "Movie One", "movie-one"),
        _row("tt0002", "Movie Two", "movie-two"),
    ]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "min_year": 2000,
            "max_year": 2024,
            "min_rating": 7.0,
            "max_rating": 10.0,
            "min_votes": 1000,
            "max_votes": 500000,
            "language": "en",
        },
    ):
        refs = await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=2,
        )

    assert len(refs) == 2
    refs_by_tconst = {ref["tconst"]: ref for ref in refs}
    assert set(refs_by_tconst) == {"tt0001", "tt0002"}
    assert refs_by_tconst["tt0001"]["title"] == "Movie One"
    assert refs_by_tconst["tt0002"]["slug"] == "movie-two"


async def test_fetch_candidate_refs_for_criteria_returns_refs(mock_db_pool):
    """Criteria-native API returns normalized ref dicts from DB rows."""
    mock_db_pool.execute.return_value = [
        _row("tt0001", "Movie One", "movie-one"),
        _row("tt0002", "Movie Two", "movie-two"),
    ]
    store = _make_store(mock_db_pool)

    refs = await store.fetch_candidate_refs_for_criteria(
        criteria={
            "min_year": 2000,
            "max_year": 2024,
            "min_rating": 7.0,
            "max_rating": 10.0,
            "min_votes": 1000,
            "max_votes": 500000,
            "language": "en",
        },
        excluded_tconsts=set(),
        limit=2,
    )

    assert len(refs) == 2
    refs_by_tconst = {ref["tconst"]: ref for ref in refs}
    assert set(refs_by_tconst) == {"tt0001", "tt0002"}
    assert refs_by_tconst["tt0002"]["slug"] == "movie-two"


async def test_fetch_candidate_refs_handles_empty_results(mock_db_pool):
    """Returns empty list when no candidates match."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
        },
    ):
        refs = await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=5,
        )

    assert refs == []
    # Should have tried all bucket steps
    assert mock_db_pool.execute.await_count == len(SELECTION_BUCKET_STEPS)


async def test_fetch_candidate_refs_deduplicates(mock_db_pool):
    """Removes duplicate tconsts from results.

    The method only returns early when ``len(deduped) >= desired_limit``,
    so limit must be <= number of unique rows.
    """
    mock_db_pool.execute.return_value = [
        _row("tt0001", "Movie One"),
        _row("tt0001", "Movie One Dupe"),
        _row("tt0002", "Movie Two"),
    ]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
        },
    ):
        refs = await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=2,
        )

    tconsts = [r["tconst"] for r in refs]
    assert sorted(tconsts) == ["tt0001", "tt0002"]
    assert len(tconsts) == len(set(tconsts))


async def test_fetch_candidate_refs_respects_limit(mock_db_pool):
    """Returns at most `limit` results after deduplication."""
    mock_db_pool.execute.return_value = [
        _row("tt0001"),
        _row("tt0002"),
        _row("tt0003"),
        _row("tt0004"),
        _row("tt0005"),
    ]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
        },
    ):
        refs = await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=2,
        )

    assert len(refs) == 2


async def test_fetch_candidate_refs_escalates_bucket_steps(mock_db_pool):
    """Tries progressively larger bucket counts when initial buckets return empty.

    Limit must be <= rows returned by the final step so the method
    returns early instead of continuing to the next (non-existent) step.
    """
    # Return empty for first 3 calls, then results on the 4th
    mock_db_pool.execute.side_effect = [
        [],  # 2 buckets
        [],  # 8 buckets
        [],  # 32 buckets
        [_row("tt9999", "Found It", "found-it")],  # 128 buckets
    ]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
        },
    ):
        refs = await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=1,
        )

    assert len(refs) == 1
    assert refs[0]["tconst"] == "tt9999"
    assert mock_db_pool.execute.await_count == 4


async def test_fetch_candidate_refs_excludes_tconsts(mock_db_pool):
    """Excluded tconsts appear in the NOT IN clause parameters."""
    mock_db_pool.execute.return_value = [_row("tt0001")]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
        },
    ):
        await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts={"tt8888", "tt9999"},
            limit=5,
        )

    call_args = mock_db_pool.execute.call_args
    params = call_args[0][1]
    query = call_args[0][0]
    assert "tconst NOT IN" in query
    # Excluded tconsts are sorted before being added to params
    assert "tt8888" in params
    assert "tt9999" in params


async def test_fetch_candidate_refs_includes_genre_clause(mock_db_pool):
    """Genre criteria produce a FULLTEXT clause in the query."""
    mock_db_pool.execute.return_value = [_row("tt0001")]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
            "genres": ["Action"],
        },
    ):
        await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=5,
        )

    call_args = mock_db_pool.execute.call_args
    query = call_args[0][0]
    assert "MATCH(genres) AGAINST(%s IN BOOLEAN MODE)" in query


def test_build_candidate_query_omits_language_clause_for_any(mock_db_pool):
    store = _make_store(mock_db_pool)

    query, params = store._build_candidate_query(
        criteria={"language": "any"},
        excluded_tconsts=set(),
        desired_limit=2,
        buckets=[1, 2],
        use_fulltext=True,
    )

    assert "language = %s" not in query
    assert "language LIKE %s" not in query
    assert "any" not in params


def test_build_candidate_query_orders_by_shuffle_key_first(mock_db_pool):
    store = _make_store(mock_db_pool)

    query, _ = store._build_candidate_query(
        criteria={"language": "en"},
        excluded_tconsts=set(),
        desired_limit=2,
        buckets=[1, 2],
        use_fulltext=True,
    )

    # Aliased columns (c.) — the query joins movie_candidates AS c so it
    # can LEFT JOIN movie_projection p for public_id.
    assert "ORDER BY c.shuffle_key, c.tconst" in query


def test_build_candidate_query_joins_movie_projection_for_public_id(mock_db_pool):
    """The candidate query LEFT JOINs movie_projection so refs carry public_id."""
    store = _make_store(mock_db_pool)

    query, _ = store._build_candidate_query(
        criteria={"language": "en"},
        excluded_tconsts=set(),
        desired_limit=2,
        buckets=[1, 2],
        use_fulltext=True,
    )

    assert "LEFT JOIN movie_projection p ON p.tconst = c.tconst" in query
    assert "p.public_id" in query


def test_build_candidate_query_language_clause_excludes_null_and_substring(mock_db_pool):
    """Language filter must be strict — no NULL bypass, no substring LIKE.

    Regression: ``title.basics.language`` contained legacy full-word strings
    (``"English"``, ``"MalayalamEnglish"``) plus 321k NULL rows. The old
    ``(language = %s OR language LIKE %s OR language IS NULL)`` clause let
    Indian films tagged as ``"MalayalamEnglish"`` and unfiltered NULL rows
    leak past an English filter. The new clause matches the ISO code plus
    a single legacy English-name spelling and nothing else.
    """
    store = _make_store(mock_db_pool)

    query, params = store._build_candidate_query(
        criteria={"language": "en"},
        excluded_tconsts=set(),
        desired_limit=2,
        buckets=[1, 2],
        use_fulltext=True,
    )

    assert "language IS NULL" not in query
    assert "language LIKE" not in query
    assert "language IN (%s, %s)" in query
    assert "en" in params
    assert "English" in params


def test_build_candidate_query_language_clause_falls_back_to_exact_for_unknown_iso(
    mock_db_pool,
):
    """Unknown ISO codes (no legacy-name mapping) use plain equality."""
    store = _make_store(mock_db_pool)

    query, params = store._build_candidate_query(
        criteria={"language": "zz"},
        excluded_tconsts=set(),
        desired_limit=2,
        buckets=[1, 2],
        use_fulltext=True,
    )

    assert "language = %s" in query
    assert "language IN" not in query
    assert "language IS NULL" not in query
    assert "zz" in params


async def test_fetch_candidate_refs_retries_like_clause_when_fulltext_missing(mock_db_pool):
    """Retries with LIKE clauses when the movie_candidates FULLTEXT index is missing."""
    mock_db_pool.execute.side_effect = [
        DatabaseError('(1191, "Can\'t find FULLTEXT index matching the column list")'),
        [_row("tt0001")],
    ]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
            "genres": ["Action", "Comedy"],
        },
    ):
        refs = await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=1,
        )

    assert refs == [
        {
            "tconst": "tt0001",
            "title": "Test Movie",
            "slug": "test-movie",
            "public_id": None,
            "year": None,
        }
    ]
    assert mock_db_pool.execute.await_count == 2
    first_query = mock_db_pool.execute.await_args_list[0].args[0]
    second_query = mock_db_pool.execute.await_args_list[1].args[0]
    assert "MATCH(genres) AGAINST(%s IN BOOLEAN MODE)" in first_query
    assert "genres LIKE %s AND genres LIKE %s" in second_query


async def test_fetch_candidate_refs_minimum_limit_is_one(mock_db_pool):
    """A limit of 0 or negative is clamped to 1."""
    mock_db_pool.execute.return_value = [_row("tt0001")]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
        },
    ):
        refs = await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=0,
        )

    assert len(refs) == 1


async def test_fetch_candidate_refs_defaults_title_to_unknown(mock_db_pool):
    """Rows with None primaryTitle get title='Unknown'."""
    mock_db_pool.execute.return_value = [
        {"tconst": "tt0001", "primaryTitle": None, "slug": None},
    ]
    store = _make_store(mock_db_pool)

    with patch(
        "movies.candidate_store.criteria_from_filters",
        return_value={
            "language": "en",
        },
    ):
        refs = await store.fetch_candidate_refs(
            filters={},
            excluded_tconsts=set(),
            limit=1,
        )

    assert refs[0]["title"] == "Unknown"


# ---------------------------------------------------------------------------
# validate_bucket_distribution
# ---------------------------------------------------------------------------


async def test_validate_bucket_distribution_raises_for_invalid_table(mock_db_pool):
    """Raises ValueError for table names not in the allowlist."""
    store = _make_store(mock_db_pool)

    with pytest.raises(ValueError, match="Invalid candidate table name"):
        await store.validate_bucket_distribution("bobby_tables; DROP TABLE--")


async def test_validate_bucket_distribution_raises_for_empty_results(mock_db_pool):
    """Raises RuntimeError when the query returns no rows."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    with pytest.raises(RuntimeError, match="produced no rows"):
        await store.validate_bucket_distribution("movie_candidates_next")


async def test_validate_bucket_distribution_raises_for_skewed_distribution(mock_db_pool):
    """Raises RuntimeError when bucket counts are outside 75%-125% of mean."""
    # Create rows where most buckets have 100 items but one has 1
    rows = [{"sample_bucket": i, "bucket_count": 100} for i in range(SAMPLE_BUCKET_COUNT)]
    rows[0]["bucket_count"] = 1  # Heavily skewed bucket
    mock_db_pool.execute.return_value = rows
    store = _make_store(mock_db_pool)

    with pytest.raises(RuntimeError, match="skew detected"):
        await store.validate_bucket_distribution("movie_candidates_next")


async def test_validate_bucket_distribution_passes_for_even_distribution(mock_db_pool):
    """Does not raise when all buckets are within tolerance."""
    rows = [{"sample_bucket": i, "bucket_count": 100} for i in range(SAMPLE_BUCKET_COUNT)]
    mock_db_pool.execute.return_value = rows
    store = _make_store(mock_db_pool)

    # Should not raise
    await store.validate_bucket_distribution("movie_candidates_next")


async def test_validate_bucket_distribution_accepts_both_allowed_tables(mock_db_pool):
    """Both movie_candidates and movie_candidates_next are valid."""
    rows = [{"sample_bucket": i, "bucket_count": 100} for i in range(SAMPLE_BUCKET_COUNT)]
    mock_db_pool.execute.return_value = rows
    store = _make_store(mock_db_pool)

    for table in _ALLOWED_CANDIDATE_TABLES:
        await store.validate_bucket_distribution(table)


async def test_validate_bucket_distribution_skew_at_upper_boundary(mock_db_pool):
    """Detects skew when a bucket is just above the 125% threshold."""
    rows = [{"sample_bucket": i, "bucket_count": 100} for i in range(SAMPLE_BUCKET_COUNT)]
    # mean = 100, upper = 125. Set one to 126 to trigger.
    rows[5]["bucket_count"] = 126
    mock_db_pool.execute.return_value = rows
    store = _make_store(mock_db_pool)

    with pytest.raises(RuntimeError, match="skew detected"):
        await store.validate_bucket_distribution("movie_candidates")


async def test_validate_bucket_distribution_missing_buckets_treated_as_zero(mock_db_pool):
    """Buckets not present in query results get count=0, triggering skew."""
    # Only report half the buckets — missing ones default to 0
    rows = [{"sample_bucket": i, "bucket_count": 100} for i in range(SAMPLE_BUCKET_COUNT // 2)]
    mock_db_pool.execute.return_value = rows
    store = _make_store(mock_db_pool)

    with pytest.raises(RuntimeError, match="skew detected"):
        await store.validate_bucket_distribution("movie_candidates_next")


# ---------------------------------------------------------------------------
# count_matching — SQL-level coverage
# ---------------------------------------------------------------------------


def _last_count_call(mock_db_pool) -> tuple[str, list]:
    """Return (sql, params) from the most recent execute() call."""
    args, kwargs = mock_db_pool.execute.call_args
    sql = args[0]
    params = args[1] if len(args) > 1 else kwargs.get("params") or []
    return sql, list(params)


async def test_count_matching_returns_total(mock_db_pool):
    mock_db_pool.execute.return_value = {"total": 1234}
    store = _make_store(mock_db_pool)
    result = await store.count_matching({})
    assert result == 1234


async def test_count_matching_returns_zero_when_no_row(mock_db_pool):
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)
    assert await store.count_matching({}) == 0


async def test_count_matching_emits_expected_clauses(mock_db_pool):
    """Generated SQL covers titleType, year/rating/votes ranges, and uses placeholders."""
    mock_db_pool.execute.return_value = {"total": 0}
    store = _make_store(mock_db_pool)
    await store.count_matching(
        {
            "min_year": 1990,
            "max_year": 2010,
            "min_rating": 7.0,
            "max_rating": 10,
            "min_votes": 1000,
            "max_votes": 500_000,
        }
    )
    sql, params = _last_count_call(mock_db_pool)
    assert "SELECT COUNT(*) AS total" in sql
    assert "FROM movie_candidates" in sql
    assert "titleType = %s" in sql
    assert "startYear BETWEEN %s AND %s" in sql
    assert "averageRating BETWEEN %s AND %s" in sql
    assert "numVotes BETWEEN %s AND %s" in sql
    # No language clause when language defaults to "any".
    assert "language" not in sql
    assert params[:7] == ["movie", 1990, 2010, 7.0, 10, 1000, 500_000]


async def test_count_matching_language_clause_includes_legacy_name(mock_db_pool):
    """Non-'any' language adds an `IN (iso, legacy_name)` clause for legacy rows."""
    mock_db_pool.execute.return_value = {"total": 0}
    store = _make_store(mock_db_pool)
    await store.count_matching({"language": "en"})
    sql, params = _last_count_call(mock_db_pool)
    assert "language IN (%s, %s)" in sql
    assert "en" in params and "English" in params


async def test_count_matching_language_any_omits_clause(mock_db_pool):
    mock_db_pool.execute.return_value = {"total": 0}
    store = _make_store(mock_db_pool)
    await store.count_matching({"language": "any"})
    sql, _params = _last_count_call(mock_db_pool)
    assert "language" not in sql


async def test_count_matching_excludes_provided_tconsts(mock_db_pool):
    """excluded_tconsts adds a `tconst NOT IN (...)` clause with parameterized values."""
    mock_db_pool.execute.return_value = {"total": 0}
    store = _make_store(mock_db_pool)
    await store.count_matching({}, excluded_tconsts={"tt1", "tt2", "tt3"})
    sql, params = _last_count_call(mock_db_pool)
    assert "tconst NOT IN (%s, %s, %s)" in sql
    # Sorted for deterministic placement.
    assert ["tt1", "tt2", "tt3"] == [p for p in params if p in {"tt1", "tt2", "tt3"}]


async def test_count_matching_no_exclusion_when_set_empty(mock_db_pool):
    mock_db_pool.execute.return_value = {"total": 0}
    store = _make_store(mock_db_pool)
    await store.count_matching({}, excluded_tconsts=set())
    sql, _params = _last_count_call(mock_db_pool)
    assert "tconst NOT IN" not in sql


async def test_count_matching_genre_clause_appended_when_genres_set(mock_db_pool):
    """When criteria carries genres, the FULLTEXT genre clause is appended."""
    mock_db_pool.execute.return_value = {"total": 0}
    store = _make_store(mock_db_pool)
    await store.count_matching({"genres": ["Action", "Drama"]})
    sql, params = _last_count_call(mock_db_pool)
    # Genre clause uses MATCH(genres) AGAINST (...) when use_fulltext=True.
    assert "MATCH" in sql or "genres" in sql
    # Last params should include the genre tokens (FULLTEXT or LIKE form).
    assert any("Action" in str(p) for p in params)


async def test_count_matching_falls_back_to_like_on_fulltext_error(mock_db_pool):
    """A FULLTEXT index error retries with the LIKE fallback."""
    from infra.errors import DatabaseError

    fulltext_err = DatabaseError("Can't find FULLTEXT index matching the column list")
    mock_db_pool.execute.side_effect = [fulltext_err, {"total": 7}]
    store = _make_store(mock_db_pool)
    result = await store.count_matching({"genres": ["Action"]})
    assert result == 7
    assert mock_db_pool.execute.await_count == 2
