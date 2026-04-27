"""Contract tests for route template context keys."""

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
def _make_route_app():
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
        manager.watched_store.list_watched = AsyncMock(
            return_value=[
                {
                    "tconst": "tt1234567",
                    "watched_at": "2026-01-02T03:04:05",
                    "primaryTitle": "Sample",
                    "startYear": 2024,
                    "slug": "sample",
                    "payload_json": {"title": "Sample", "year": "2024"},
                }
            ]
        )
        manager.watched_store.count = AsyncMock(return_value=1)
        manager.watched_store.list_watched_filtered = AsyncMock(
            return_value=[
                {
                    "tconst": "tt1234567",
                    "watched_at": "2026-01-02T03:04:05",
                    "primaryTitle": "Sample",
                    "startYear": 2024,
                    "slug": "sample",
                    "payload_json": {"title": "Sample", "year": "2024"},
                }
            ]
        )
        manager.watched_store.count_filtered = AsyncMock(return_value=1)
        manager.watched_store.available_filter_chips = AsyncMock(
            return_value={"decades": ["2020s"], "genres": ["Drama"], "ratings": []}
        )
        navigator = MagicMock()
        navigator.prev_stack_length = MagicMock(return_value=2)
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


class TestRouteViewContracts:
    @pytest.mark.asyncio
    async def test_movie_detail_keeps_template_context_keys(self):
        with _make_route_app() as (app, _manager):
            with patch(
                "nextreel.web.routes.movies.render_template",
                AsyncMock(return_value="<html>movie</html>"),
            ) as render, patch(
                "nextreel.web.routes.shared.resolve_to_tconst",
                new=AsyncMock(return_value="tt1234567"),
            ):
                async with app.test_request_context("/movie/sample-2024-abc123"):
                    g.navigation_state = _nav_state()
                    g.correlation_id = "corr-1"

                    response = await routes.movie_detail("sample-2024-abc123")

        assert response == "<html>movie</html>"
        render.assert_awaited_once()
        _, kwargs = render.await_args
        assert set(kwargs) >= {"movie", "previous_count"}

    @pytest.mark.asyncio
    async def test_watched_list_keeps_template_context_keys(self):
        with _make_route_app() as (app, _manager):
            with patch(
                "nextreel.web.routes.watched.render_template",
                AsyncMock(return_value="<html>watched</html>"),
            ) as render:
                async with app.test_request_context("/watched?page=1&per_page=60"):
                    g.navigation_state = _nav_state(user_id="user-123")

                    response = await routes.watched_list_page()

        assert response == "<html>watched</html>"
        render.assert_awaited_once()
        _, kwargs = render.await_args
        assert set(kwargs) >= {"movies", "total", "filter_chips", "has_more", "pagination"}
