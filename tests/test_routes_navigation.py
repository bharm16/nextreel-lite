"""Route tests for navigation endpoints — next/previous/filtered/logout."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import TEST_ENV


def _make_app():
    with patch.dict(os.environ, TEST_ENV), \
         patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.next_movie = AsyncMock(return_value=None)
        manager.previous_movie = AsyncMock(return_value=None)
        manager.filtered_movie = AsyncMock(return_value=None)
        manager.render_movie_by_tconst = AsyncMock(return_value="<html>movie</html>")
        manager.get_current_movie_tconst = MagicMock(return_value=None)
        manager.logout = AsyncMock()

        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        return app, manager


class TestNextMovieRoute:
    async def test_post_without_csrf_rejected(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.post("/next_movie")
            assert response.status_code == 403

    async def test_get_returns_405(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/next_movie")
            assert response.status_code == 405


class TestPreviousMovieRoute:
    async def test_post_without_csrf_rejected(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.post("/previous_movie")
            assert response.status_code == 403

    async def test_get_returns_405(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/previous_movie")
            assert response.status_code == 405


class TestFilteredMovieRoute:
    async def test_post_without_csrf_rejected(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                data={"year_min": "2000"},
            )
            assert response.status_code == 403


class TestLogoutRoute:
    async def test_post_without_csrf_rejected(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.post("/logout")
            assert response.status_code == 403

    async def test_get_returns_405(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/logout")
            assert response.status_code == 405


class TestMovieDetailRoute:
    async def test_valid_tconst_returns_200(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/movie/tt1234567")
            assert response.status_code == 200

    async def test_invalid_tconst_returns_400(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/movie/invalid")
            assert response.status_code in (400, 404)

    async def test_sql_injection_tconst_rejected(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/movie/tt1; DROP TABLE movies")
            assert response.status_code in (400, 404)


class TestFiltersRoute:
    async def test_get_returns_200(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/filters")
            assert response.status_code == 200
