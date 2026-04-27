"""Tests for nextreel.application.movie_navigator._movie_ref helper
and the NavigationOutcome dataclass.

Focused on the lightweight ref-dict shape stored in navigation state
and the outcome construction that carries ref data through to the
redirect helper. Broader navigator behavior (queue, prev/future stacks,
filter resets) lives in tests/application/test_movie_navigator_extended.py.
"""

from __future__ import annotations

from nextreel.application.movie_navigator import NavigationOutcome, _movie_ref


def test_movie_ref_includes_public_id_when_provided():
    ref = _movie_ref(
        {
            "tconst": "tt0393109",
            "imdb_id": "tt0393109",
            "title": "The Departed",
            "slug": "the-departed-2006",
            "public_id": "a8fk3j",
        }
    )
    assert ref["public_id"] == "a8fk3j"


def test_movie_ref_falls_back_to_none_when_missing():
    ref = _movie_ref(
        {
            "tconst": "tt0393109",
            "title": "The Departed",
            "slug": "the-departed-2006",
        }
    )
    assert ref.get("public_id") is None


def test_movie_ref_returns_five_key_dict_with_year():
    ref = _movie_ref(
        {
            "tconst": "tt0393109",
            "title": "The Departed",
            "slug": "the-departed-2006",
            "public_id": "a8fk3j",
            "year": "2006",
        }
    )
    assert set(ref.keys()) == {"tconst", "title", "slug", "public_id", "year"}
    assert ref["year"] == "2006"


def test_movie_ref_coerces_int_year_to_string():
    """startYear arrives as int from DB; ref must expose it as string."""
    ref = _movie_ref(
        {
            "tconst": "tt0393109",
            "title": "The Departed",
            "slug": "the-departed-2006",
            "startYear": 2006,
        }
    )
    assert ref["year"] == "2006"


def test_navigation_outcome_from_ref_populates_all_fields():
    outcome = NavigationOutcome.from_ref(
        {
            "tconst": "tt0393109",
            "title": "The Departed",
            "slug": "the-departed-2006",
            "public_id": "a8fk3j",
            "year": "2006",
        }
    )
    assert outcome.tconst == "tt0393109"
    assert outcome.public_id == "a8fk3j"
    assert outcome.title == "The Departed"
    assert outcome.year == "2006"
    assert outcome.state_conflict is False


def test_navigation_outcome_from_ref_coerces_int_year_to_string():
    outcome = NavigationOutcome.from_ref(
        {
            "tconst": "tt0393109",
            "title": "The Departed",
            "public_id": "a8fk3j",
            "year": 2006,
        }
    )
    assert outcome.year == "2006"


def test_navigation_outcome_from_ref_handles_none():
    outcome = NavigationOutcome.from_ref(None)
    assert outcome.tconst is None
    assert outcome.public_id is None
    assert outcome.title is None
    assert outcome.year is None
    assert outcome.state_conflict is False


def test_navigation_outcome_from_ref_propagates_state_conflict():
    outcome = NavigationOutcome.from_ref(
        {"tconst": "tt0393109", "public_id": "a8fk3j", "title": "The Departed"},
        state_conflict=True,
    )
    assert outcome.state_conflict is True
    assert outcome.tconst == "tt0393109"


def test_navigation_outcome_from_ref_none_with_state_conflict():
    outcome = NavigationOutcome.from_ref(None, state_conflict=True)
    assert outcome.tconst is None
    assert outcome.state_conflict is True
