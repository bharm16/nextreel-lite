"""Route tests for navigation endpoints — next/previous/filtered/logout."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nextreel.application.movie_navigator import NavigationOutcome
from tests.helpers import TEST_ENV


def _make_app():
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.next_movie = AsyncMock(return_value=None)
        manager.previous_movie = AsyncMock(return_value=None)
        manager.apply_filters = AsyncMock(return_value=None)
        manager.db_pool = object()
        manager.filtered_movie = AsyncMock(return_value=None)
        manager.render_movie_by_tconst = AsyncMock(return_value="<html>movie</html>")
        manager.get_current_movie_tconst = MagicMock(return_value=None)
        manager.logout = AsyncMock()
        # /movie/<tconst> now calls projection_store.fetch_renderable_payload
        # and watched_store.is_watched concurrently.
        manager.projection_store = MagicMock()
        manager.projection_store.fetch_renderable_payload = AsyncMock(
            return_value={
                "title": "Sample",
                "year": "2024",
                "genres": "Drama",
                "directors": "Dir",
                "rating": 0.0,
                "votes": 0,
                "plot": "",
                "poster_url": None,
                "backdrop_url": None,
                "cast": [],
                "tmdb_id": 1,
                "imdb_id": "tt1234567",
                "_full": True,
                "projection_state": "ready",
            }
        )
        manager.projection_store.coordinator = MagicMock()
        manager.projection_store.coordinator.tmdb_helper = None
        manager.projection_store.coordinator._inflight_enrichment = {}
        manager.projection_store.coordinator.has_inflight = MagicMock(return_value=False)
        manager.watched_store = MagicMock()
        manager.watched_store.is_watched = AsyncMock(return_value=False)
        manager.watched_store.list_watched = AsyncMock(return_value=[])
        manager.watched_store.count = AsyncMock(return_value=0)
        navigator = MagicMock()
        navigator.prev_stack_length = MagicMock(return_value=0)
        manager._navigator = navigator

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        return app, manager


def _nav_state(*, user_id=None):
    return SimpleNamespace(
        session_id="test-session-id",
        csrf_token="test-csrf-token",
        filters={},
        user_id=user_id,
    )


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
        manager.next_movie = AsyncMock(
            return_value=NavigationOutcome(tconst=None, state_conflict=True)
        )
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
            response = await client.post(
                "/previous_movie", headers={"X-CSRFToken": "test-csrf-token"}
            )
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

    async def test_logged_in_valid_apply_persists_exclude_watched_false_before_applying_filters(
        self,
    ):
        app, manager = _make_app()
        state = _nav_state(user_id="user-123")
        calls = []

        async def persist_preference(*args, **kwargs):
            calls.append("persist")

        async def apply_filters(*args, **kwargs):
            calls.append("apply")
            return NavigationOutcome(tconst="tt1234567")

        manager.apply_filters = AsyncMock(side_effect=apply_filters)
        async with app.app_context():
            client = app.test_client()
            with (
                patch("nextreel.web.routes.navigation._current_state", return_value=state),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watched_default",
                    new_callable=AsyncMock,
                    side_effect=persist_preference,
                ) as set_exclude_watched_default,
            ):
                response = await client.post(
                    "/filtered_movie",
                    headers={"X-CSRFToken": "test-csrf-token"},
                    form={
                        "year_min": "2000",
                        "exclude_watched": "off",
                    },
                )

        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/tt1234567")
        set_exclude_watched_default.assert_awaited_once_with(
            manager.db_pool,
            "user-123",
            False,
        )
        manager.apply_filters.assert_awaited_once()
        applied_state, applied_filters = manager.apply_filters.await_args.args[:2]
        assert applied_state is state
        assert applied_filters["exclude_watched"] is False
        assert calls == ["persist", "apply"]

    async def test_logged_in_valid_apply_persists_exclude_watched_true(self):
        app, manager = _make_app()
        state = _nav_state(user_id="user-123")
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst="tt1234567"))
        async with app.app_context():
            client = app.test_client()
            with (
                patch("nextreel.web.routes.navigation._current_state", return_value=state),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watched_default",
                    new_callable=AsyncMock,
                ) as set_exclude_watched_default,
            ):
                response = await client.post(
                    "/filtered_movie",
                    headers={"X-CSRFToken": "test-csrf-token"},
                    form={
                        "year_min": "2000",
                        "exclude_watched": "on",
                    },
                )

        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/tt1234567")
        set_exclude_watched_default.assert_awaited_once_with(
            manager.db_pool,
            "user-123",
            True,
        )
        manager.apply_filters.assert_awaited_once()
        applied_filters = manager.apply_filters.await_args.args[1]
        assert applied_filters["exclude_watched"] is True

    async def test_invalid_filters_do_not_persist_exclude_watched_or_apply_filters(self):
        app, manager = _make_app()
        state = _nav_state(user_id="user-123")
        async with app.app_context():
            client = app.test_client()
            with (
                patch("nextreel.web.routes.navigation._current_state", return_value=state),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watched_default",
                    new_callable=AsyncMock,
                ) as set_exclude_watched_default,
            ):
                response = await client.post(
                    "/filtered_movie",
                    headers={"X-CSRFToken": "test-csrf-token"},
                    form={
                        "year_min": "2025",
                        "year_max": "1990",
                        "exclude_watched": "off",
                    },
                )
                body = await response.get_data(as_text=True)

        assert response.status_code == 400
        assert "Fix the highlighted filters and try again." in body
        set_exclude_watched_default.assert_not_awaited()
        manager.apply_filters.assert_not_awaited()

    async def test_anonymous_valid_apply_does_not_persist_exclude_watched(self):
        app, manager = _make_app()
        state = _nav_state(user_id=None)
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst="tt1234567"))
        async with app.app_context():
            client = app.test_client()
            with (
                patch("nextreel.web.routes.navigation._current_state", return_value=state),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watched_default",
                    new_callable=AsyncMock,
                ) as set_exclude_watched_default,
            ):
                response = await client.post(
                    "/filtered_movie",
                    headers={"X-CSRFToken": "test-csrf-token"},
                    form={
                        "year_min": "2000",
                        "exclude_watched": "off",
                    },
                )

        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/tt1234567")
        set_exclude_watched_default.assert_not_awaited()
        manager.apply_filters.assert_awaited_once()
        applied_state, applied_filters = manager.apply_filters.await_args.args[:2]
        assert applied_state is state
        assert applied_filters["exclude_watched"] is False

    # ── JSON response branch tests (AJAX from filter drawer) ──

    async def test_json_validation_errors_return_400(self):
        app, manager = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={
                    "X-CSRFToken": "test-csrf-token",
                    "Accept": "application/json",
                },
                form={"year_min": "2025", "year_max": "1990"},
            )
            assert response.status_code == 400
            data = await response.get_json()
            assert data["ok"] is False
            assert "year_min" in data["errors"] or "year_max" in data["errors"]
            manager.apply_filters.assert_not_awaited()

    async def test_json_success_returns_redirect_url(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst="tt9999999"))
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={
                    "X-CSRFToken": "test-csrf-token",
                    "Accept": "application/json",
                },
                form={"year_min": "2000"},
            )
            assert response.status_code == 200
            data = await response.get_json()
            assert data["ok"] is True
            assert "/movie/tt9999999" in data["redirect"]

    async def test_json_no_tconst_returns_error(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst=None))
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={
                    "X-CSRFToken": "test-csrf-token",
                    "Accept": "application/json",
                },
                form={"year_min": "2000"},
            )
            data = await response.get_json()
            assert data["ok"] is False
            assert "errors" in data

    async def test_json_no_matches_returns_error(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=None)
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={
                    "X-CSRFToken": "test-csrf-token",
                    "Accept": "application/json",
                },
                form={"year_min": "2000"},
            )
            data = await response.get_json()
            assert data["ok"] is False
            assert "form" in data["errors"]


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
