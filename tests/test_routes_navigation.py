"""Route tests for navigation endpoints — next/previous/filtered/logout."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from movie_navigator import NavigationOutcome
from tests.helpers import TEST_ENV


def _make_app():
    with patch.dict(os.environ, TEST_ENV), \
         patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.next_movie = AsyncMock(return_value=None)
        manager.previous_movie = AsyncMock(return_value=None)
        manager.apply_filters = AsyncMock(return_value=None)
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

    async def test_redirects_to_movie_from_navigation_outcome(self):
        app, manager = _make_app()
        manager.next_movie = AsyncMock(return_value=NavigationOutcome(tconst="tt1234567"))
        async with app.app_context():
            client = app.test_client()
            response = await client.post("/next_movie", headers={"X-CSRFToken": "test-csrf-token"})
            assert response.status_code == 303
            assert response.headers["Location"].endswith("/movie/tt1234567")

    async def test_redirects_conflict_to_home_when_no_tconst(self):
        app, manager = _make_app()
        manager.next_movie = AsyncMock(return_value=NavigationOutcome(tconst=None, state_conflict=True))
        async with app.app_context():
            client = app.test_client()
            response = await client.post("/next_movie", headers={"X-CSRFToken": "test-csrf-token"})
            assert response.status_code == 303
            assert response.headers["Location"].endswith("/?state_conflict=1")


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

    async def test_redirects_from_navigation_outcome(self):
        app, manager = _make_app()
        manager.previous_movie = AsyncMock(return_value=NavigationOutcome(tconst="tt7654321"))
        async with app.app_context():
            client = app.test_client()
            response = await client.post("/previous_movie", headers={"X-CSRFToken": "test-csrf-token"})
            assert response.status_code == 303
            assert response.headers["Location"].endswith("/movie/tt7654321")


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

    async def test_invalid_filters_render_form_with_400_without_calling_manager(self):
        app, manager = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={
                    "year_min": "2025",
                    "year_max": "1990",
                },
            )
            body = await response.get_data(as_text=True)

        assert response.status_code == 400
        assert "Fix the highlighted filters and try again." in body
        assert "Earliest year must be less than or equal to latest year." in body
        manager.apply_filters.assert_not_awaited()

    async def test_invalid_filters_with_no_genres_show_all_genres_notice(self):
        app, manager = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={
                    "year_min": "bad-year",
                },
            )
            body = await response.get_data(as_text=True)

        assert response.status_code == 400
        assert "No genres selected. Nextreel will use all genres." in body
        manager.apply_filters.assert_not_awaited()

    async def test_redirects_to_movie_when_filters_match(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst="tt1234567"))
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={"year_min": "2000"},
            )
            assert response.status_code == 303
            assert response.headers["Location"].endswith("/movie/tt1234567")


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
