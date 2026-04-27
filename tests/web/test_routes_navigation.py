"""Route tests for navigation endpoints — next/previous/filtered/logout."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

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
        manager.render_movie_by_tconst = AsyncMock(return_value="<html>movie</html>")
        manager.get_current_movie_tconst = MagicMock(return_value=None)
        manager.logout = AsyncMock()
        # /movie/<tconst> now calls projection_store.fetch_renderable_payload
        # and watched_store.is_watched concurrently.
        manager.projection_store = MagicMock()
        manager.projection_store.fetch_renderable_payload = AsyncMock(
            return_value={
                "title": "Sample",
                "primaryTitle": "Sample",
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
                "public_id": "abc123",
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
        manager.watchlist_store = MagicMock()
        manager.watchlist_store.is_in_watchlist = AsyncMock(return_value=False)
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


def _authenticate_app(app, manager, *, user_id="user-123"):
    """Wire a navigation_state_store mock so before_request loads a state with user_id set.

    Mirrors the authenticated-mode setup in tests/web/test_account_routes.py::_make_account_app.
    Kept inline here because the navigation tests need the projection/navigator mocks
    that _make_app() provides, which _make_account_app does not.
    """
    logged_in_state = _nav_state(user_id=user_id)
    store = AsyncMock()
    store.load_for_request = AsyncMock(return_value=(logged_in_state, False))
    store.set_user_id = AsyncMock()
    store.bind_user = AsyncMock()
    manager.start = AsyncMock()
    manager.navigation_state_store = store
    app.navigation_state_store = store
    return logged_in_state


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
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt1234567",
                "public_id": "abc123",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
        async with app.app_context():
            client = app.test_client()
            response = await client.post("/next_movie", headers={"X-CSRFToken": "test-csrf-token"})
            assert response.status_code == 303
            assert response.headers["Location"].endswith("/movie/sample-2024-abc123")

    async def test_next_movie_redirects_to_public_id_path(self):
        """The next_movie outcome redirect must use /movie/<slug>-<public_id>, not tconst."""
        app, manager = _make_app()
        manager.next_movie = AsyncMock(
            return_value=NavigationOutcome(tconst="tt0393109")
        )
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt0393109",
                "public_id": "a8fk3j",
                "payload_json": '{"primaryTitle": "The Departed", "year": "2006"}',
            }
        )
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/next_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
            )
            assert response.status_code == 303
            assert response.headers["Location"].endswith("/movie/the-departed-2006-a8fk3j")

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

    async def test_lazy_creates_projection_for_first_time_candidate(self):
        """Bug repro: navigator picks a candidate with no projection row yet.

        Before the fix, _build_movie_url_for_tconst saw select_row return None,
        gave up, and redirected the user back to the landing page instead of the
        movie detail page. The fix calls ensure_core_projection lazily so the URL
        builder always has a public_id to point at.
        """
        app, manager = _make_app()
        manager.next_movie = AsyncMock(return_value=NavigationOutcome(tconst="tt0012349"))

        # First select_row returns None (no projection yet); after
        # ensure_core_projection runs, the second call returns the row.
        select_calls = [
            None,
            {
                "tconst": "tt0012349",
                "public_id": "newpid",
                "payload_json": '{"primaryTitle": "The Kid", "year": "1921"}',
            },
        ]
        manager.projection_store.select_row = AsyncMock(side_effect=select_calls)
        ensure_core = AsyncMock(
            return_value={"tconst": "tt0012349", "public_id": "newpid"}
        )
        manager.projection_store.ensure_core_projection = ensure_core

        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/next_movie", headers={"X-CSRFToken": "test-csrf-token"}
            )

        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/the-kid-1921-newpid")
        ensure_core.assert_awaited_once_with("tt0012349")
        assert manager.projection_store.select_row.await_count == 2

    async def test_redirect_uses_outcome_fields_without_db_lookup_when_present(self):
        """Hot path: outcome carries public_id+title+year → no DB lookup.

        When the navigator builds the outcome from a candidate ref that
        already has public_id from the LEFT JOIN to movie_projection, the
        redirect helper builds the URL directly from outcome fields and
        never calls projection_store.select_row.
        """
        app, manager = _make_app()
        manager.next_movie = AsyncMock(
            return_value=NavigationOutcome(
                tconst="tt0393109",
                public_id="a8fk3j",
                title="The Departed",
                year="2006",
            )
        )
        select_row_mock = AsyncMock()
        manager.projection_store.select_row = select_row_mock

        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/next_movie", headers={"X-CSRFToken": "test-csrf-token"}
            )

        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/the-departed-2006-a8fk3j")
        select_row_mock.assert_not_awaited()

    async def test_redirect_falls_back_to_db_lookup_when_outcome_missing_public_id(
        self,
    ):
        """Cold path: outcome carries tconst only (first-time candidate).

        With public_id=None on the outcome (the LEFT JOIN to
        movie_projection returned NULL because the projection row hasn't
        been created yet), the redirect helper falls back to
        _build_movie_url_for_tconst, which lazy-creates the projection
        row via ensure_core_projection and reads back public_id.
        """
        app, manager = _make_app()
        manager.next_movie = AsyncMock(
            return_value=NavigationOutcome(
                tconst="tt0012349",
                public_id=None,
                title="The Kid",
                year="1921",
            )
        )
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt0012349",
                "public_id": "newpid",
                "payload_json": '{"primaryTitle": "The Kid", "year": "1921"}',
            }
        )

        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/next_movie", headers={"X-CSRFToken": "test-csrf-token"}
            )

        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/the-kid-1921-newpid")
        manager.projection_store.select_row.assert_awaited()

    async def test_falls_back_to_home_when_lazy_create_yields_no_row(self):
        """If ensure_core_projection runs but the row still cannot be found
        (e.g. tconst not in title.basics), redirect home instead of crashing."""
        app, manager = _make_app()
        manager.next_movie = AsyncMock(return_value=NavigationOutcome(tconst="tt9999999"))
        manager.projection_store.select_row = AsyncMock(return_value=None)
        manager.projection_store.ensure_core_projection = AsyncMock(return_value=None)

        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/next_movie", headers={"X-CSRFToken": "test-csrf-token"}
            )

        assert response.status_code == 303
        assert urlparse(response.headers["Location"]).path == "/"


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
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt7654321",
                "public_id": "xyz789",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/previous_movie", headers={"X-CSRFToken": "test-csrf-token"}
            )
            assert response.status_code == 303
            assert response.headers["Location"].endswith("/movie/sample-2024-xyz789")


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

    async def test_invalid_filters_return_json_400_without_calling_manager(self):
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
            data = await response.get_json()

        assert response.status_code == 400
        assert data["ok"] is False
        assert "year_min" in data["errors"] or "year_max" in data["errors"]
        manager.apply_filters.assert_not_awaited()

    async def test_redirects_to_movie_when_filters_match(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst="tt1234567"))
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt1234567",
                "public_id": "abc123",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={"year_min": "2000"},
            )
            assert response.status_code == 303
            assert response.headers["Location"].endswith("/movie/sample-2024-abc123")

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
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt1234567",
                "public_id": "abc123",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
        async with app.app_context():
            client = app.test_client()
            with (
                patch("nextreel.web.routes.navigation._current_state", return_value=state),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watched_default",
                    new_callable=AsyncMock,
                    side_effect=persist_preference,
                ) as set_exclude_watched_default,
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watchlist_default",
                    new_callable=AsyncMock,
                ),
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
        assert response.headers["Location"].endswith("/movie/sample-2024-abc123")
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

    async def test_logged_in_valid_apply_persists_exclude_watchlist_false_before_applying_filters(
        self,
    ):
        """When exclude_watchlist=off submitted, persist False to user prefs."""
        # Mirrors test_logged_in_valid_apply_persists_exclude_watched_false_before_applying_filters.
        app, manager = _make_app()
        state = _nav_state(user_id="user-123")
        calls = []

        async def persist_preference(*args, **kwargs):
            calls.append("persist")

        async def apply_filters(*args, **kwargs):
            calls.append("apply")
            return NavigationOutcome(tconst="tt1234567")

        manager.apply_filters = AsyncMock(side_effect=apply_filters)
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt1234567",
                "public_id": "abc123",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
        async with app.app_context():
            client = app.test_client()
            with (
                patch("nextreel.web.routes.navigation._current_state", return_value=state),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watched_default",
                    new_callable=AsyncMock,
                ),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watchlist_default",
                    new_callable=AsyncMock,
                    side_effect=persist_preference,
                ) as set_exclude_watchlist_default,
            ):
                response = await client.post(
                    "/filtered_movie",
                    headers={"X-CSRFToken": "test-csrf-token"},
                    form={
                        "year_min": "2000",
                        "exclude_watched": "on",
                        "exclude_watchlist": "off",
                    },
                )

        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/sample-2024-abc123")
        set_exclude_watchlist_default.assert_awaited_once_with(
            manager.db_pool,
            "user-123",
            False,
        )
        manager.apply_filters.assert_awaited_once()
        applied_state, applied_filters = manager.apply_filters.await_args.args[:2]
        assert applied_state is state
        assert applied_filters["exclude_watchlist"] is False
        assert calls == ["persist", "apply"]

    async def test_logged_in_valid_apply_persists_exclude_watched_true(self):
        app, manager = _make_app()
        state = _nav_state(user_id="user-123")
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst="tt1234567"))
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt1234567",
                "public_id": "abc123",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
        async with app.app_context():
            client = app.test_client()
            with (
                patch("nextreel.web.routes.navigation._current_state", return_value=state),
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watched_default",
                    new_callable=AsyncMock,
                ) as set_exclude_watched_default,
                patch(
                    "nextreel.web.routes.navigation.set_exclude_watchlist_default",
                    new_callable=AsyncMock,
                ),
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
        assert response.headers["Location"].endswith("/movie/sample-2024-abc123")
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
                data = await response.get_json()

        assert response.status_code == 400
        assert data["ok"] is False
        set_exclude_watched_default.assert_not_awaited()
        manager.apply_filters.assert_not_awaited()

    async def test_anonymous_valid_apply_does_not_persist_exclude_watched(self):
        app, manager = _make_app()
        state = _nav_state(user_id=None)
        manager.apply_filters = AsyncMock(return_value=NavigationOutcome(tconst="tt1234567"))
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt1234567",
                "public_id": "abc123",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
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
        assert response.headers["Location"].endswith("/movie/sample-2024-abc123")
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
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt9999999",
                "public_id": "qwerty",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
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
            assert "/movie/sample-2024-qwerty" in data["redirect"]

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

    async def test_html_no_matches_redirects_to_current_movie(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=None)
        manager.get_current_movie_tconst = MagicMock(return_value="tt7654321")
        manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt7654321",
                "public_id": "xyz789",
                "payload_json": '{"primaryTitle": "Sample", "year": "2024"}',
            }
        )
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={"year_min": "2000"},
            )
        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/sample-2024-xyz789")

    async def test_html_no_matches_redirects_to_home_when_no_current_movie(self):
        app, manager = _make_app()
        manager.apply_filters = AsyncMock(return_value=None)
        manager.get_current_movie_tconst = MagicMock(return_value=None)
        async with app.app_context():
            client = app.test_client()
            response = await client.post(
                "/filtered_movie",
                headers={"X-CSRFToken": "test-csrf-token"},
                form={"year_min": "2000"},
            )
        assert response.status_code == 303
        assert urlparse(response.headers["Location"]).path == "/"


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
    async def test_valid_slug_with_id_returns_200(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            with patch(
                "nextreel.web.routes.shared.resolve_to_tconst",
                new=AsyncMock(return_value="tt1234567"),
            ):
                # Mocked payload's title="Sample" / year="2024" → slug "sample-2024".
                response = await client.get("/movie/sample-2024-abc123")
                assert response.status_code == 200

    async def test_invalid_path_returns_404(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/movie/invalid")
            assert response.status_code == 404

    async def test_imdb_tconst_path_returns_404(self):
        """Old /movie/tt... URLs are a hard break under the new scheme."""
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/movie/tt1234567")
            assert response.status_code == 404

    async def test_sql_injection_path_rejected(self):
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/movie/tt1; DROP TABLE movies")
            assert response.status_code in (400, 404)


class TestFiltersRoute:
    async def test_get_returns_404(self):
        """The /filters page was removed in favor of the drawer."""
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/filters")
            assert response.status_code == 404


class TestDrawerSaveAsDefaultButton:
    async def test_button_rendered_for_logged_in_user(self):
        app, manager = _make_app()
        _authenticate_app(app, manager, user_id="user-123")
        async with app.app_context():
            client = app.test_client()
            with patch(
                "nextreel.web.routes.shared.resolve_to_tconst",
                new=AsyncMock(return_value="tt1234567"),
            ):
                # Mocked payload's title="Sample" / year="2024" → slug "sample-2024".
                response = await client.get("/movie/sample-2024-abc123")
                body = await response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'formaction="/account/preferences/filters/save"' in body
        assert "Save as default" in body

    async def test_button_absent_for_anonymous_user(self):
        # Default _make_app leaves navigation_state_store as None,
        # so before_request uses build_test_navigation_state() (user_id=None).
        app, _ = _make_app()
        async with app.app_context():
            client = app.test_client()
            with patch(
                "nextreel.web.routes.shared.resolve_to_tconst",
                new=AsyncMock(return_value="tt1234567"),
            ):
                response = await client.get("/movie/sample-2024-abc123")
                body = await response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Save as default" not in body
        assert "/account/preferences/filters/save" not in body
