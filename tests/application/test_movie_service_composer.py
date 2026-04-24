"""Tests for MovieManager dependency injection and app composition."""

from __future__ import annotations

from unittest.mock import MagicMock

from nextreel.application.movie_service import MovieManager


def test_movie_manager_accepts_injected_dependencies():
    from nextreel.application.movie_service import HomePrewarmService

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
    from nextreel.bootstrap.movie_manager_factory import build_movie_manager

    mock_pool = MagicMock()
    mock_tmdb = MagicMock()
    mock_candidate = MagicMock()
    mock_projection = MagicMock()
    mock_projection.coordinator = MagicMock()
    mock_watched = MagicMock()
    mock_renderer = MagicMock()
    mock_prewarm = MagicMock()

    manager = build_movie_manager(
        {"host": "localhost"},
        db_pool_cls=MagicMock(return_value=mock_pool),
        tmdb_helper_cls=MagicMock(return_value=mock_tmdb),
        candidate_store_cls=MagicMock(return_value=mock_candidate),
        projection_store_cls=MagicMock(return_value=mock_projection),
        watched_store_cls=MagicMock(return_value=mock_watched),
        renderer_cls=MagicMock(return_value=mock_renderer),
        home_prewarm_service_cls=MagicMock(return_value=mock_prewarm),
    )

    assert isinstance(manager, MovieManager)
    assert manager.db_pool is mock_pool
    assert manager.tmdb_helper is mock_tmdb
    assert manager.candidate_store is mock_candidate
    assert manager.projection_store is mock_projection
    assert manager.watched_store is mock_watched
    assert manager._renderer is mock_renderer
    assert manager._home_prewarm_service is mock_prewarm
