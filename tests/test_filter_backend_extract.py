import pytest
from scripts.filter_backend import extract_movie_filter_criteria


class DummyForm:
    """Simple stand-in for a web form object."""

    def __init__(self, data):
        self._data = data

    def get(self, key):
        return self._data.get(key)

    def getlist(self, key):
        return self._data.get(key, [])


def test_default_language_is_english():
    form = DummyForm({'year_min': '2000', 'year_max': '2005'})
    criteria = extract_movie_filter_criteria(form)
    assert criteria['min_year'] == 2000
    assert criteria['max_year'] == 2005
    assert criteria['language'] == 'en'


def test_extract_all_fields():
    form = DummyForm({
        'year_min': '1990',
        'year_max': '1995',
        'imdb_score_min': '7.1',
        'imdb_score_max': '8.5',
        'num_votes_min': '100',
        'num_votes_max': '1000',
        'language': 'fr',
        'genres[]': ['Action', 'Drama']
    })
    criteria = extract_movie_filter_criteria(form)
    assert criteria == {
        'min_year': 1990,
        'max_year': 1995,
        'min_rating': 7.1,
        'max_rating': 8.5,
        'min_votes': 100,
        'max_votes': 1000,
        'genres': ['Action', 'Drama'],
        'language': 'fr'
    }
