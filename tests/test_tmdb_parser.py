"""Tests for the extracted TMDbResponseParser."""

from movies.tmdb_parser import TMDbResponseParser

IMAGE_BASE = "https://image.tmdb.org/t/p/"


def _parser():
    return TMDbResponseParser(IMAGE_BASE)


# ── parse_cast ───────────────────────────────────────────────────────

def test_parse_cast_extracts_names_and_images():
    data = {
        "credits": {
            "cast": [
                {"name": "Alice", "profile_path": "/alice.jpg", "character": "Hero"},
                {"name": "Bob", "profile_path": None, "character": "Villain"},
            ]
        }
    }
    result = _parser().parse_cast(data, limit=10)
    assert len(result) == 2
    assert result[0]["name"] == "Alice"
    assert result[0]["image_url"] == f"{IMAGE_BASE}w185/alice.jpg"
    assert result[1]["image_url"] is None


def test_parse_cast_respects_limit():
    data = {"credits": {"cast": [{"name": f"Actor{i}", "character": "X"} for i in range(20)]}}
    result = _parser().parse_cast(data, limit=3)
    assert len(result) == 3


def test_parse_cast_empty_credits():
    assert _parser().parse_cast({}) == []


# ── parse_directors ──────────────────────────────────────────────────

def test_parse_directors():
    data = {
        "credits": {
            "crew": [
                {"name": "Spielberg", "job": "Director"},
                {"name": "Kaminski", "job": "Director of Photography"},
            ]
        }
    }
    assert _parser().parse_directors(data) == ["Spielberg"]


def test_parse_directors_empty():
    assert _parser().parse_directors({}) == []


# ── parse_trailer ────────────────────────────────────────────────────

def test_parse_trailer_finds_youtube():
    data = {
        "videos": {
            "results": [
                {"site": "Vimeo", "type": "Trailer", "key": "abc"},
                {"site": "YouTube", "type": "Trailer", "key": "xyz123"},
            ]
        }
    }
    assert _parser().parse_trailer(data) == "https://www.youtube.com/watch?v=xyz123"


def test_parse_trailer_none_when_missing():
    assert _parser().parse_trailer({}) is None


# ── parse_age_rating ─────────────────────────────────────────────────

def test_parse_age_rating_prefers_us():
    data = {
        "release_dates": {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [{"certification": "PG-13"}],
                },
                {
                    "iso_3166_1": "GB",
                    "release_dates": [{"certification": "12A"}],
                },
            ]
        }
    }
    assert _parser().parse_age_rating(data) == "PG-13"


def test_parse_age_rating_fallback():
    data = {
        "release_dates": {
            "results": [
                {"iso_3166_1": "FR", "release_dates": [{"certification": "12"}]},
            ]
        }
    }
    assert _parser().parse_age_rating(data) == "12"


def test_parse_age_rating_not_rated():
    assert _parser().parse_age_rating({}) == "Not Rated"


# ── parse_images ─────────────────────────────────────────────────────

def test_parse_images():
    data = {
        "images": {
            "posters": [{"file_path": "/p1.jpg"}, {"file_path": "/p2.jpg"}],
            "backdrops": [{"file_path": "/b1.jpg"}],
        }
    }
    result = _parser().parse_images(data, limit=1)
    assert len(result["posters"]) == 1
    assert result["posters"][0].endswith("/p1.jpg")
    assert len(result["backdrops"]) == 1


# ── parse_keywords ───────────────────────────────────────────────────

def test_parse_keywords():
    data = {"keywords": {"keywords": [{"name": "sci-fi"}, {"name": "robots"}]}}
    assert _parser().parse_keywords(data) == ["sci-fi", "robots"]


# ── parse_recommendations ────────────────────────────────────────────

def test_parse_recommendations():
    data = {
        "recommendations": {
            "results": [
                {"id": 1, "title": "Rec1", "release_date": "2020-05-01", "poster_path": "/r.jpg", "vote_average": 8.0},
            ]
        }
    }
    result = _parser().parse_recommendations(data, limit=5)
    assert len(result) == 1
    assert result[0]["tmdb_id"] == 1
    assert result[0]["year"] == "2020"


# ── parse_external_ids ───────────────────────────────────────────────

def test_parse_external_ids():
    data = {"external_ids": {"imdb_id": "tt1234567", "twitter_id": "movie"}}
    result = _parser().parse_external_ids(data)
    assert "imdb_url" in result
    assert "twitter_url" in result
    assert "facebook_url" not in result


# ── parse_collection ─────────────────────────────────────────────────

def test_parse_collection():
    data = {"belongs_to_collection": {"id": 42, "name": "Saga", "poster_path": "/c.jpg"}}
    result = _parser().parse_collection(data)
    assert result["id"] == 42
    assert result["poster_url"].endswith("/c.jpg")


def test_parse_collection_none():
    assert _parser().parse_collection({}) is None


# ── parse_watch_providers ────────────────────────────────────────────

def test_parse_watch_providers():
    data = {
        "watch/providers": {
            "results": {
                "US": {
                    "flatrate": [{"provider_name": "Netflix", "logo_path": "/nf.jpg"}],
                    "link": "https://justwatch.com/test",
                }
            }
        }
    }
    result = _parser().parse_watch_providers(data, region="US")
    assert result["stream"][0]["provider_name"] == "Netflix"
    assert result["justwatch_link"] == "https://justwatch.com/test"


def test_parse_watch_providers_missing_region():
    data = {"watch/providers": {"results": {}}}
    assert _parser().parse_watch_providers(data) is None


# ── parse_key_crew ───────────────────────────────────────────────────

def test_parse_key_crew():
    data = {
        "credits": {
            "crew": [
                {"name": "Writer1", "job": "Screenplay"},
                {"name": "Composer1", "job": "Original Music Composer"},
                {"name": "DP1", "job": "Director of Photography"},
            ]
        }
    }
    result = _parser().parse_key_crew(data)
    assert result["writers"] == ["Writer1"]
    assert result["composer"] == "Composer1"
    assert result["cinematographer"] == "DP1"
