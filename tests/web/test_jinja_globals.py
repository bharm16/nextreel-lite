"""Tests for Jinja globals registered by ``init_routes``."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import TEST_ENV


def _make_app():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.mark.asyncio
async def test_movie_url_global_registered():
    """The Jinja global movie_url(movie) is registered and produces correct paths."""
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool = MagicMock()

        app = _make_app()
        movie_url = app.jinja_env.globals.get("movie_url")
        assert movie_url is not None
        movie = {"primaryTitle": "The Departed", "year": "2006", "public_id": "a8fk3j"}
        assert movie_url(movie) == "/movie/the-departed-2006-a8fk3j"


@pytest.mark.asyncio
async def test_movie_url_global_handles_missing_public_id():
    """movie_url returns ``/`` when the dict lacks a public_id."""
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool = MagicMock()

        app = _make_app()
        movie_url = app.jinja_env.globals.get("movie_url")
        assert movie_url is not None
        assert movie_url({"primaryTitle": "X", "year": "2024"}) == "/"
        assert movie_url(None) == "/"
        assert movie_url({}) == "/"


@pytest.mark.asyncio
async def test_movie_url_global_falls_back_to_title_field():
    """movie_url accepts ``title`` and ``startYear`` as fallback keys."""
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool = MagicMock()

        app = _make_app()
        movie_url = app.jinja_env.globals.get("movie_url")
        movie = {"title": "Pulp Fiction", "startYear": 1994, "public_id": "abc123"}
        assert movie_url(movie) == "/movie/pulp-fiction-1994-abc123"
