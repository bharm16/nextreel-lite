from __future__ import annotations

from infra.pool import DatabaseConnectionPool
from movies.candidate_store import CandidateStore
from movies.projection_store import ProjectionStore
from movies.tmdb_client import TMDbHelper
from movies.watched_store import WatchedStore
from movies.watchlist_store import WatchlistStore
from nextreel.application.movie_service import HomePrewarmService, MovieManager
from nextreel.web.movie_renderer import MovieRenderer


def build_movie_manager(
    db_config: dict[str, object],
    *,
    db_pool_cls=DatabaseConnectionPool,
    tmdb_helper_cls=TMDbHelper,
    candidate_store_cls=CandidateStore,
    projection_store_cls=ProjectionStore,
    watched_store_cls=WatchedStore,
    watchlist_store_cls=WatchlistStore,
    renderer_cls=MovieRenderer,
    home_prewarm_service_cls=HomePrewarmService,
    movie_manager_cls=MovieManager,
) -> MovieManager:
    """Compose MovieManager runtime dependencies."""
    db_pool = db_pool_cls(db_config)
    tmdb_helper = tmdb_helper_cls()
    candidate_store = candidate_store_cls(db_pool)
    projection_store = projection_store_cls(db_pool, tmdb_helper=tmdb_helper)
    watched_store = watched_store_cls(db_pool)
    watchlist_store = watchlist_store_cls(db_pool)
    renderer = renderer_cls(projection_store)
    home_prewarm_service = home_prewarm_service_cls()
    return movie_manager_cls(
        db_config=db_config,
        db_pool=db_pool,
        tmdb_helper=tmdb_helper,
        candidate_store=candidate_store,
        projection_store=projection_store,
        watched_store=watched_store,
        watchlist_store=watchlist_store,
        renderer=renderer,
        home_prewarm_service=home_prewarm_service,
    )
