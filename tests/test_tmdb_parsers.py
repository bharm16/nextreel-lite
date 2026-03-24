"""Tests for TMDbHelper parse methods and get_movie_full."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from movies.tmdb_client import TMDbHelper


# ---------------------------------------------------------------------------
# Fixture: sample combined TMDb response (as returned by get_movie_full)
# ---------------------------------------------------------------------------


def _sample_combined_response():
    """Return a realistic combined TMDb response dict."""
    return {
        "id": 550,
        "title": "Fight Club",
        "overview": "An insomniac office worker...",
        "tagline": "Mischief. Mayhem. Soap.",
        "release_date": "1999-10-15",
        "runtime": 139,
        "budget": 63000000,
        "revenue": 101200000,
        "vote_average": 8.4,
        "vote_count": 25000,
        "poster_path": "/poster.jpg",
        "original_language": "en",
        "status": "Released",
        "homepage": "https://example.com/fightclub",
        "genres": [{"id": 18, "name": "Drama"}, {"id": 53, "name": "Thriller"}],
        "spoken_languages": [{"iso_639_1": "en", "english_name": "English"}],
        "production_countries": [{"iso_3166_1": "US", "name": "United States of America"}],
        "belongs_to_collection": {
            "id": 1234,
            "name": "Fight Club Collection",
            "poster_path": "/coll_poster.jpg",
            "backdrop_path": "/coll_back.jpg",
        },
        "production_companies": [
            {"id": 508, "name": "Regency Enterprises", "logo_path": "/logo.png", "origin_country": "US"}
        ],
        "credits": {
            "cast": [
                {"name": "Brad Pitt", "character": "Tyler Durden", "profile_path": "/brad.jpg", "order": 0},
                {"name": "Edward Norton", "character": "Narrator", "profile_path": "/ed.jpg", "order": 1},
                {"name": "Helena Bonham Carter", "character": "Marla Singer", "profile_path": None, "order": 2},
            ],
            "crew": [
                {"name": "David Fincher", "job": "Director", "department": "Directing"},
                {"name": "Jim Uhls", "job": "Screenplay", "department": "Writing"},
                {"name": "Chuck Palahniuk", "job": "Writer", "department": "Writing"},
                {"name": "The Dust Brothers", "job": "Original Music Composer", "department": "Sound"},
                {"name": "Jeff Cronenweth", "job": "Director of Photography", "department": "Camera"},
                {"name": "James Haygood", "job": "Editor", "department": "Editing"},
            ],
        },
        "videos": {
            "results": [
                {"site": "YouTube", "type": "Trailer", "key": "SUXWAEX2jlg", "name": "Official Trailer"},
                {"site": "YouTube", "type": "Teaser", "key": "abc123", "name": "Teaser"},
            ]
        },
        "images": {
            "posters": [{"file_path": "/poster1.jpg"}, {"file_path": "/poster2.jpg"}],
            "backdrops": [{"file_path": "/back1.jpg"}, {"file_path": "/back2.jpg"}],
        },
        "release_dates": {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"certification": "R", "type": 3, "release_date": "1999-10-15"},
                    ],
                },
                {
                    "iso_3166_1": "GB",
                    "release_dates": [
                        {"certification": "18", "type": 3, "release_date": "1999-11-12"},
                    ],
                },
            ]
        },
        "watch/providers": {
            "results": {
                "US": {
                    "link": "https://www.justwatch.com/us/movie/fight-club",
                    "flatrate": [
                        {"provider_name": "Netflix", "logo_path": "/netflix.png", "provider_id": 8},
                    ],
                    "rent": [
                        {"provider_name": "Apple TV", "logo_path": "/apple.png", "provider_id": 2},
                    ],
                    "buy": [
                        {"provider_name": "Amazon", "logo_path": "/amazon.png", "provider_id": 3},
                    ],
                    "ads": [
                        {"provider_name": "Tubi", "logo_path": "/tubi.png", "provider_id": 73},
                    ],
                }
            }
        },
        "keywords": {
            "keywords": [
                {"id": 1, "name": "fight"},
                {"id": 2, "name": "underground"},
                {"id": 3, "name": "soap"},
            ]
        },
        "recommendations": {
            "results": [
                {
                    "id": 680,
                    "title": "Pulp Fiction",
                    "release_date": "1994-09-10",
                    "poster_path": "/pulp.jpg",
                    "vote_average": 8.5,
                },
                {
                    "id": 13,
                    "title": "Forrest Gump",
                    "release_date": "1994-06-23",
                    "poster_path": None,
                    "vote_average": 8.8,
                },
            ]
        },
        "external_ids": {
            "imdb_id": "tt0137523",
            "wikidata_id": "Q190050",
            "facebook_id": "FightClub",
            "instagram_id": "fightclub",
            "twitter_id": "fightclub",
        },
    }


# ---------------------------------------------------------------------------
# Parse method tests
# ---------------------------------------------------------------------------


class TestParseCast:
    def test_extracts_top_cast(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        cast = h.parse_cast(data, limit=2)
        assert len(cast) == 2
        assert cast[0]["name"] == "Brad Pitt"
        assert cast[0]["character"] == "Tyler Durden"
        assert "w185" in cast[0]["image_url"]

    def test_handles_missing_profile_path(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        cast = h.parse_cast(data, limit=3)
        assert cast[2]["image_url"] is None

    def test_empty_credits(self):
        h = TMDbHelper("key")
        assert h.parse_cast({}) == []
        assert h.parse_cast({"credits": {}}) == []


class TestParseDirectors:
    def test_extracts_directors(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        assert h.parse_directors(data) == ["David Fincher"]

    def test_empty_crew(self):
        h = TMDbHelper("key")
        assert h.parse_directors({}) == []


class TestParseKeyCrew:
    def test_extracts_writers_composer_dp(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        crew = h.parse_key_crew(data)
        assert "Jim Uhls" in crew["writers"]
        assert "Chuck Palahniuk" in crew["writers"]
        assert crew["composer"] == "The Dust Brothers"
        assert crew["cinematographer"] == "Jeff Cronenweth"

    def test_empty_data(self):
        h = TMDbHelper("key")
        assert h.parse_key_crew({}) == {}


class TestParseTrailer:
    def test_extracts_youtube_trailer(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        url = h.parse_trailer(data)
        assert url == "https://www.youtube.com/watch?v=SUXWAEX2jlg"

    def test_no_trailer(self):
        h = TMDbHelper("key")
        assert h.parse_trailer({}) is None
        assert h.parse_trailer({"videos": {"results": []}}) is None

    def test_skips_non_trailer_types(self):
        h = TMDbHelper("key")
        data = {"videos": {"results": [{"site": "YouTube", "type": "Teaser", "key": "x"}]}}
        assert h.parse_trailer(data) is None


class TestParseImages:
    def test_extracts_limited_images(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        images = h.parse_images(data, limit=1)
        assert len(images["posters"]) == 1
        assert len(images["backdrops"]) == 1
        assert "original" in images["posters"][0]

    def test_empty_data(self):
        h = TMDbHelper("key")
        images = h.parse_images({})
        assert images == {"posters": [], "backdrops": []}


class TestParseAgeRating:
    def test_extracts_us_rating(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        assert h.parse_age_rating(data) == "R"

    def test_fallback_to_other_country(self):
        h = TMDbHelper("key")
        data = {
            "release_dates": {
                "results": [
                    {"iso_3166_1": "GB", "release_dates": [{"certification": "18"}]},
                ]
            }
        }
        assert h.parse_age_rating(data) == "18"

    def test_not_rated_when_empty(self):
        h = TMDbHelper("key")
        assert h.parse_age_rating({}) == "Not Rated"
        assert h.parse_age_rating({"release_dates": {"results": []}}) == "Not Rated"


class TestParseWatchProviders:
    def test_extracts_all_categories(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        wp = h.parse_watch_providers(data)
        assert "stream" in wp
        assert "rent" in wp
        assert "buy" in wp
        assert "ads" in wp
        assert wp["stream"][0]["provider_name"] == "Netflix"
        assert wp["justwatch_link"].startswith("https://")

    def test_returns_none_for_missing_region(self):
        h = TMDbHelper("key")
        data = {"watch/providers": {"results": {}}}
        assert h.parse_watch_providers(data) is None

    def test_empty_data(self):
        h = TMDbHelper("key")
        assert h.parse_watch_providers({}) is None


class TestParseKeywords:
    def test_extracts_keyword_names(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        kw = h.parse_keywords(data)
        assert kw == ["fight", "underground", "soap"]

    def test_empty_data(self):
        h = TMDbHelper("key")
        assert h.parse_keywords({}) == []


class TestParseRecommendations:
    def test_extracts_recommendations(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        recs = h.parse_recommendations(data, limit=10)
        assert len(recs) == 2
        assert recs[0]["title"] == "Pulp Fiction"
        assert recs[0]["year"] == "1994"
        assert "w342" in recs[0]["poster_url"]
        assert recs[1]["poster_url"] is None

    def test_empty_data(self):
        h = TMDbHelper("key")
        assert h.parse_recommendations({}) == []


class TestParseExternalIds:
    def test_extracts_all_ids(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        ext = h.parse_external_ids(data)
        assert ext["imdb_url"] == "https://www.imdb.com/title/tt0137523/"
        assert "wikidata.org" in ext["wikidata_url"]
        assert "facebook.com" in ext["facebook_url"]
        assert "instagram.com" in ext["instagram_url"]
        assert "x.com" in ext["twitter_url"]

    def test_partial_ids(self):
        h = TMDbHelper("key")
        data = {"external_ids": {"imdb_id": "tt999"}}
        ext = h.parse_external_ids(data)
        assert "imdb_url" in ext
        assert "facebook_url" not in ext

    def test_empty_data(self):
        h = TMDbHelper("key")
        assert h.parse_external_ids({}) == {}


class TestParseCollection:
    def test_extracts_collection(self):
        h = TMDbHelper("key")
        data = _sample_combined_response()
        coll = h.parse_collection(data)
        assert coll["name"] == "Fight Club Collection"
        assert "w185" in coll["poster_url"]

    def test_no_collection(self):
        h = TMDbHelper("key")
        assert h.parse_collection({}) is None
        assert h.parse_collection({"belongs_to_collection": None}) is None


# ---------------------------------------------------------------------------
# get_movie_full integration
# ---------------------------------------------------------------------------


class TestGetMovieFull:
    def test_calls_with_append_to_response(self):
        async def run():
            h = TMDbHelper("key")
            h._get = AsyncMock(return_value=_sample_combined_response())
            result = await h.get_movie_full(550)

            h._get.assert_awaited_once()
            call_args = h._get.call_args
            assert call_args[0][0] == "movie/550"
            params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params", {})
            # Check that append_to_response is passed
            if isinstance(params, dict):
                atr = params.get("append_to_response", "")
            else:
                atr = call_args[1].get("params", {}).get("append_to_response", "")
            assert "credits" in atr
            assert "videos" in atr
            assert "keywords" in atr
            assert "recommendations" in atr
            assert "external_ids" in atr
            assert result["title"] == "Fight Club"

        asyncio.run(run())
