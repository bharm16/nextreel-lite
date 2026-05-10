import pytest
from movies.filter_parser import extract_movie_filter_criteria


class DummyForm:
    """Simple stand-in for a web form object."""

    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def getlist(self, key):
        return self._data.get(key, [])


def test_default_language_is_english():
    form = DummyForm({"year_min": "2000", "year_max": "2005"})
    criteria = extract_movie_filter_criteria(form)
    assert criteria["min_year"] == 2000
    assert criteria["max_year"] == 2005
    assert criteria["language"] == "en"


def test_extract_all_fields():
    form = DummyForm(
        {
            "year_min": "1990",
            "year_max": "1995",
            "imdb_score_min": "7.1",
            "imdb_score_max": "8.5",
            "num_votes_min": "100",
            "num_votes_max": "1000",
            "language": "fr",
            "genres[]": ["Action", "Drama"],
        }
    )
    criteria = extract_movie_filter_criteria(form)
    assert criteria == {
        "min_year": 1990,
        "max_year": 1995,
        "min_rating": 7.1,
        "max_rating": 8.5,
        "min_votes": 100,
        "max_votes": 1000,
        "genres": ["Action", "Drama"],
        "language": "fr",
    }


class TestExcludeGenresExtraction:
    def test_omitted_when_no_exclude_genres_submitted(self):
        form = DummyForm({"genres[]": ["Action"]})
        criteria = extract_movie_filter_criteria(form)
        assert "exclude_genres" not in criteria

    def test_populates_when_exclude_genres_submitted(self):
        form = DummyForm({"exclude_genres[]": ["Drama", "War"]})
        criteria = extract_movie_filter_criteria(form)
        assert criteria["exclude_genres"] == ["Drama", "War"]

    def test_filters_invalid_genres_against_allow_list(self):
        """Reject names that aren't in VALID_GENRES — same allow-list as include path."""
        form = DummyForm({"exclude_genres[]": ["Drama", "NotAGenre", "War"]})
        criteria = extract_movie_filter_criteria(form)
        assert criteria["exclude_genres"] == ["Drama", "War"]

    def test_drops_non_string_entries(self):
        form = DummyForm({"exclude_genres[]": ["Drama", None, 42, "War"]})
        criteria = extract_movie_filter_criteria(form)
        assert criteria["exclude_genres"] == ["Drama", "War"]

    def test_empty_list_omits_key(self):
        form = DummyForm({"exclude_genres[]": []})
        criteria = extract_movie_filter_criteria(form)
        assert "exclude_genres" not in criteria

    def test_include_and_exclude_can_coexist(self):
        form = DummyForm(
            {"genres[]": ["Action"], "exclude_genres[]": ["Drama"]}
        )
        criteria = extract_movie_filter_criteria(form)
        assert criteria["genres"] == ["Action"]
        assert criteria["exclude_genres"] == ["Drama"]
