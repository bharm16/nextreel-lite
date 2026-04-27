"""Tests for the / (home/landing) route integration."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import TEST_ENV


@pytest.fixture
def test_client():
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool = MagicMock()
        # Home route no longer issues an extra public_id lookup — landing
        # film comes pre-populated from fetch_random_landing_film, and the
        # hardcoded fallback pool intentionally has no public_id (the
        # template hides the "See this film" CTA in that case).
        manager.db_pool.execute = AsyncMock(return_value=None)

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        yield app.test_client()


@pytest.mark.asyncio
async def test_home_route_renders_with_db_sourced_film(test_client):
    fake_film = {
        "tconst": "tt0109424",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=fake_film),
    ):
        response = await test_client.get("/")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        # Title must render
        assert "Chungking Express" in body
        # Backdrop URL must appear (in the inline style or element)
        assert "foo.jpg" in body
        # Year must appear (in the credit corner)
        assert "1994" in body
        # Credit corner must render
        assert "Film still: Chungking Express" in body


@pytest.mark.asyncio
async def test_home_route_falls_back_when_db_empty(test_client):
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=None),
    ):
        response = await test_client.get("/")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        # One of the three fallback films must render
        fallback_titles = ("2001: A Space Odyssey", "Alien", "Pulp Fiction")
        assert any(t in body for t in fallback_titles)


@pytest.mark.asyncio
async def test_api_landing_film_returns_json(test_client):
    """Unfiltered call returns a JSON film payload."""
    fake_film = {
        "tconst": "tt0109424",
        "public_id": "abc123",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=fake_film),
    ):
        resp = await test_client.get("/api/landing-film")
        assert resp.status_code == 200
        payload = await resp.get_json()
        # tconst is the internal IMDb id — never expose it in public JSON.
        assert "tconst" not in payload
        assert payload["public_id"] == "abc123"
        assert "backdrop_url" in payload
        assert payload["backdrop_url"].startswith("https://image.tmdb.org/")
        # Server builds the canonical /movie/<slug>-<public_id> path so the
        # client doesn't have to reproduce the slugifier.
        assert payload["movie_path"] == "/movie/chungking-express-1994-abc123"


@pytest.mark.asyncio
async def test_api_landing_film_with_genre_filter(test_client):
    """Genre filter routes through the filtered fetch path."""
    fake_film = {
        "tconst": "tt0109424",
        "public_id": "abc123",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=fake_film),
    ):
        resp = await test_client.get("/api/landing-film?genre=Drama")
        assert resp.status_code == 200
        payload = await resp.get_json()
        assert "tconst" not in payload
        assert payload["public_id"] == "abc123"
        assert payload["movie_path"].startswith("/movie/")
        assert payload["movie_path"].endswith("-abc123")


@pytest.mark.asyncio
async def test_api_landing_film_returns_204_when_no_match(test_client):
    """When the filter combo has no matches, return 204 with empty body."""
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=None),
    ):
        resp = await test_client.get("/api/landing-film?genre=Drama&decade=1970s")
        assert resp.status_code == 204
        body = await resp.get_data()
        assert body == b""


@pytest.mark.asyncio
async def test_api_landing_film_drops_invalid_params(test_client):
    """Invalid filter values are silently dropped (returns whatever the
    unfiltered query returns)."""
    fake_film = {
        "tconst": "tt0062622",
        "public_id": "abc123",
        "title": "2001: A Space Odyssey",
        "year": "1968",
        "director": "Stanley Kubrick",
        "runtime": "149 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/9yTOU2SvTfAEHDPEG5qraLoe4MI.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=fake_film),
    ):
        resp = await test_client.get("/api/landing-film?genre=NotAGenre")
        # No criteria after dropping, so unfiltered path runs and returns 200.
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_landing_film_backfills_public_id_when_missing(test_client):
    """When the service returns a film without public_id, the route should
    fetch one via public_id_for_tconst and include it in the JSON response.
    """
    film_without_pid = {
        "tconst": "tt0109424",
        "public_id": None,
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=film_without_pid),
    ), patch(
        "nextreel.web.routes.movies.public_id_for_tconst",
        new=AsyncMock(return_value="abcdef"),
    ):
        resp = await test_client.get("/api/landing-film")
        assert resp.status_code == 200
        payload = await resp.get_json()
        assert payload["public_id"] == "abcdef"
        # movie_path is built using the backfilled public_id.
        assert payload["movie_path"].endswith("-abcdef")


@pytest.mark.asyncio
async def test_api_landing_film_returns_204_when_public_id_unresolvable(test_client):
    """If both the service and the backfill lookup fail to produce a
    public_id, the endpoint must return 204 — shipping a payload without
    public_id leaves the client with no way to build a canonical /movie URL,
    so the secondary CTA would 404.
    """
    film_without_pid = {
        "tconst": "tt0109424",
        "public_id": None,
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=film_without_pid),
    ), patch(
        "nextreel.web.routes.movies.public_id_for_tconst",
        new=AsyncMock(return_value=None),
    ):
        resp = await test_client.get("/api/landing-film")
        assert resp.status_code == 204
        assert (await resp.get_data()) == b""


# ---------------------------------------------------------------------------
# Task 4: home() route reads URL params, passes criteria/filters to template
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_home_with_no_url_params_unfiltered(test_client):
    """Bare / returns 200 and does NOT contain any active-filter hidden inputs."""
    fake_film = {
        "tconst": "tt0109424",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=fake_film),
    ):
        response = await test_client.get("/")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        # No active-filter hidden inputs should appear on an unfiltered home page
        assert 'name="genres[]"' not in body


@pytest.mark.asyncio
async def test_home_with_genre_param_marks_pill_active(test_client):
    """/?genre=Drama — the Drama pill should have aria-pressed="true"."""
    fake_film = {
        "tconst": "tt0109424",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=fake_film),
    ):
        response = await test_client.get("/?genre=Drama")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        # The Drama pill must have aria-pressed="true"
        assert 'data-filter-value="Drama"' in body
        assert 'aria-pressed="true"' in body


@pytest.mark.asyncio
async def test_home_with_filters_active_form_posts_to_filtered_movie(test_client):
    """/?genre=Drama&decade=1990s — active filters should populate hidden inputs
    for /filtered_movie POST form."""
    fake_film = {
        "tconst": "tt0109424",
        "title": "Chungking Express",
        "year": "1994",
        "director": "Wong Kar-wai",
        "runtime": "102 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
    }
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=fake_film),
    ):
        response = await test_client.get("/?genre=Drama&decade=1990s")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert 'action="/filtered_movie"' in body
        assert '<input type="hidden" name="genres[]" value="Drama"' in body
        assert '<input type="hidden" name="year_min" value="1990"' in body
        assert '<input type="hidden" name="year_max" value="1999"' in body


@pytest.mark.asyncio
async def test_home_with_filters_no_match_renders_empty_state(test_client):
    """When fetch_random_landing_film returns None and filters are present, render
    empty state rather than using the fallback pool."""
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=None),
    ):
        response = await test_client.get("/?genre=Drama&decade=1970s")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "No films match these filters" in body
        assert '<a class="landing-cta-primary" href="/">Clear filters</a>' in body
        assert "See this film" not in body


@pytest.mark.asyncio
async def test_home_with_no_filters_no_match_uses_fallback_pool(test_client):
    """When fetch_random_landing_film returns None and NO filters are present,
    the fallback pool kicks in — no empty-state message."""
    with patch(
        "nextreel.web.routes.movies.fetch_random_landing_film",
        new=AsyncMock(return_value=None),
    ):
        response = await test_client.get("/")
        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "No films match these filters" not in body
