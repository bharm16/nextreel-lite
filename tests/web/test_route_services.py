"""Unit tests for extracted route-level services and presenters."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestMovieDetailService:
    @pytest.mark.asyncio
    async def test_get_returns_view_model_with_movie_previous_count_and_watch_flag(self):
        from nextreel.web.route_services import MovieDetailService

        movie_manager = MagicMock()
        movie_manager.watched_store = MagicMock()
        movie_manager.watched_store.is_watched = AsyncMock(return_value=True)
        movie_manager.watchlist_store = MagicMock()
        movie_manager.watchlist_store.is_in_watchlist = AsyncMock(return_value=False)
        movie_manager.projection_store = MagicMock()
        movie_manager.projection_store.fetch_renderable_payload = AsyncMock(
            return_value={"title": "Sample"}
        )
        movie_manager.prev_stack_length = MagicMock(return_value=3)

        view_model = await MovieDetailService().get(
            movie_manager=movie_manager,
            state=SimpleNamespace(session_id="sess-1"),
            user_id="user-123",
            tconst="tt1234567",
        )

        assert view_model.movie == {"title": "Sample", "tconst": "tt1234567"}
        assert view_model.previous_count == 3
        assert view_model.is_watched is True

    @pytest.mark.asyncio
    async def test_get_returns_none_when_projection_payload_missing(self):
        from nextreel.web.route_services import MovieDetailService

        movie_manager = MagicMock()
        movie_manager.watched_store = MagicMock()
        movie_manager.watched_store.is_watched = AsyncMock(return_value=False)
        movie_manager.watchlist_store = MagicMock()
        movie_manager.watchlist_store.is_in_watchlist = AsyncMock(return_value=False)
        movie_manager.projection_store = MagicMock()
        movie_manager.projection_store.fetch_renderable_payload = AsyncMock(return_value=None)
        movie_manager.prev_stack_length = MagicMock(return_value=3)

        view_model = await MovieDetailService().get(
            movie_manager=movie_manager,
            state=SimpleNamespace(session_id="sess-1"),
            user_id="user-123",
            tconst="tt1234567",
        )

        assert view_model is None

    @pytest.mark.asyncio
    async def test_movie_detail_service_returns_is_in_watchlist(self):
        from nextreel.web.route_services import MovieDetailService

        movie_manager = MagicMock()
        movie_manager.watched_store.is_watched = AsyncMock(return_value=False)
        movie_manager.watchlist_store.is_in_watchlist = AsyncMock(return_value=True)
        movie_manager.projection_store.fetch_renderable_payload = AsyncMock(
            return_value={"tconst": "tt1", "_full": True}
        )
        movie_manager.prev_stack_length = MagicMock(return_value=0)

        svc = MovieDetailService()
        vm = await svc.get(movie_manager=movie_manager, state=None, user_id="u1", tconst="tt1")

        assert vm.is_in_watchlist is True


class TestWatchedListPresenter:
    def test_build_preserves_movies_stats_and_pagination_shape(self):
        from nextreel.web.route_services import WatchedListPresenter

        raw_rows = [
            {
                "tconst": "tt1234567",
                "watched_at": datetime(2026, 1, 15, 12, 0, 0),
                "primaryTitle": "Sample",
                "startYear": 2024,
                "slug": "sample",
                "payload_json": {"title": "Sample", "year": "2024", "rating": "7.5"},
            },
            {
                "tconst": None,
                "watched_at": datetime(2026, 1, 15, 12, 0, 0),
                "payload_json": {},
            },
        ]

        view_model = WatchedListPresenter().build(
            raw_rows=raw_rows,
            total_count=1,
            page=2,
            per_page=1,
            now=datetime(2026, 1, 20, 8, 0, 0),
        )

        assert view_model.movies == [
            {
                "tconst": "tt1234567",
                "slug": "sample",
                "title": "Sample",
                "year": 2024,
                "poster_url": "/static/img/poster-placeholder.svg",
                "tmdb_rating": 7.5,
                "watched_at": "2026-01-15T12:00:00",
            }
        ]
        assert view_model.stats == {
            "total": 1,
            "this_month": 1,
            "avg_year": 2024,
            "top_decade": "2020s",
        }
        assert view_model.total == 1
        assert view_model.pagination == {
            "page": 2,
            "per_page": 1,
            "total_pages": 1,
            "has_prev": True,
            "has_next": False,
        }


class TestWatchlistPresenter:
    def test_watchlist_presenter_normalizes_added_at_field(self):
        from nextreel.web.route_services import WatchlistPresenter

        presenter = WatchlistPresenter()
        rows = [
            {
                "tconst": "tt1",
                "primaryTitle": "Film",
                "startYear": 1995,
                "added_at": datetime(2026, 4, 14),
                "payload_json": '{"poster_url": "/x.jpg"}',
            }
        ]
        vm = presenter.build(
            raw_rows=rows, total_count=1, page=1, per_page=20,
            now=datetime(2026, 4, 25),
        )

        assert len(vm.movies) == 1
        assert vm.movies[0]["added_at"].startswith("2026-04-14")
        assert vm.total == 1

