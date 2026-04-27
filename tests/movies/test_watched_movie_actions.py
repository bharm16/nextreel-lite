"""Regression tests for watched actions on the movie detail page."""

from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import g

import nextreel.web.routes as routes
from tests.helpers import TEST_ENV


@contextmanager
def _make_app():
    with patch.dict(os.environ, TEST_ENV, clear=False), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
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
        manager.watched_store = MagicMock()
        manager.watched_store.is_watched = AsyncMock(return_value=False)
        manager.watched_store.add = AsyncMock()
        manager.watched_store.remove = AsyncMock()
        manager.watchlist_store = MagicMock()
        manager.watchlist_store.is_in_watchlist = AsyncMock(return_value=False)
        navigator = MagicMock()
        navigator.prev_stack_length = MagicMock(return_value=0)
        manager._navigator = navigator

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        yield app, manager


def _nav_state(*, user_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        csrf_token="test-csrf-token",
        session_id="test-session-id",
        user_id=user_id,
        filters={},
    )


class TestWatchedMovieActions:
    @pytest.mark.asyncio
    async def test_movie_detail_renders_watched_action_with_public_id(self):
        """Watched action URLs use the movie's public_id (post-migration)."""
        with _make_app() as (app, _manager), patch(
            "nextreel.web.routes.shared.resolve_to_tconst",
            new=AsyncMock(return_value="tt1234567"),
        ):
            async with app.test_request_context("/movie/sample-2024-abc123"):
                g.navigation_state = _nav_state(user_id="user-123")
                g.correlation_id = "corr-1"

                response = await routes.movie_detail("sample-2024-abc123")

        assert '/watched/add/abc123' in response

    @pytest.mark.asyncio
    async def test_add_to_watched_returns_json_for_ajax_requests(self):
        with _make_app() as (app, manager), patch(
            "infra.route_helpers.check_rate_limit",
            AsyncMock(return_value=True),
        ), patch(
            "nextreel.web.routes.shared.resolve_to_tconst",
            new=AsyncMock(return_value="tt1234567"),
        ):
            async with app.test_request_context(
                "/watched/add/a8fk3j",
                method="POST",
                headers={
                    "Accept": "application/json",
                    "X-CSRFToken": "test-csrf-token",
                },
            ):
                g.navigation_state = _nav_state(user_id="user-123")

                response = await routes.add_to_watched("a8fk3j")
                data = await response.get_json()

        assert response.status_code == 200
        # tconst intentionally omitted from the response — only the opaque
        # public_id is exposed to clients.
        assert data == {
            "ok": True,
            "is_watched": True,
            "public_id": "a8fk3j",
        }
        manager.watched_store.add.assert_awaited_once_with("user-123", "tt1234567")
