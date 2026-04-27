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
        # Metadata tokens must appear
        assert "1994" in body
        assert "Wong Kar-wai" in body
        assert "102 min" in body


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
