"""Tests for MovieManager dependency injection and app composition."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from movie_service import MovieManager


def test_movie_manager_accepts_injected_dependencies():
    from movie_service import HomePrewarmService

    db_pool = MagicMock()
    tmdb_helper = MagicMock()
    candidate_store = MagicMock()
    projection_store = MagicMock()
    projection_store.coordinator = MagicMock()
    watched_store = MagicMock()
    renderer = MagicMock()
    prewarm_service = HomePrewarmService()

    manager = MovieManager(
        db_config={"host": "localhost"},
        db_pool=db_pool,
        tmdb_helper=tmdb_helper,
        candidate_store=candidate_store,
        projection_store=projection_store,
        watched_store=watched_store,
        renderer=renderer,
        home_prewarm_service=prewarm_service,
    )

    assert manager.db_pool is db_pool
    assert manager.tmdb_helper is tmdb_helper
    assert manager.candidate_store is candidate_store
    assert manager.projection_store is projection_store
    assert manager.watched_store is watched_store
    assert manager._renderer is renderer
    assert manager.projection_coordinator is projection_store.coordinator
    assert manager._home_prewarm_service is prewarm_service


def test_build_movie_manager_composes_runtime_dependencies():
    with (
        patch("app.DatabaseConnectionPool") as mock_pool_cls,
        patch("app.TMDbHelper") as mock_tmdb_cls,
        patch("app.CandidateStore") as mock_candidate_cls,
        patch("app.ProjectionStore") as mock_projection_cls,
        patch("app.WatchedStore") as mock_watched_cls,
        patch("app.MovieRenderer") as mock_renderer_cls,
        patch("app.HomePrewarmService") as mock_prewarm_cls,
    ):
        mock_pool = mock_pool_cls.return_value
        mock_tmdb = mock_tmdb_cls.return_value
        mock_candidate = mock_candidate_cls.return_value
        mock_projection = mock_projection_cls.return_value
        mock_projection.coordinator = MagicMock()
        mock_watched = mock_watched_cls.return_value
        mock_renderer = mock_renderer_cls.return_value
        mock_prewarm = mock_prewarm_cls.return_value

        from app import build_movie_manager

        manager = build_movie_manager({"host": "localhost"})

    assert isinstance(manager, MovieManager)
    assert manager.db_pool is mock_pool
    assert manager.tmdb_helper is mock_tmdb
    assert manager.candidate_store is mock_candidate
    assert manager.projection_store is mock_projection
    assert manager.watched_store is mock_watched
    assert manager._renderer is mock_renderer
    assert manager._home_prewarm_service is mock_prewarm
