"""Verify MovieManager exposes watchlist_store and propagates cache."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from movies.watchlist_store import WatchlistStore
from nextreel.application.movie_service import MovieManager


def test_movie_manager_has_default_watchlist_store():
    mgr = MovieManager(
        db_config={"host": "x", "user": "y", "password": "z", "db": "w", "port": 3306},
        db_pool=MagicMock(),
        tmdb_helper=MagicMock(),
        candidate_store=MagicMock(),
        projection_store=MagicMock(coordinator=None),
        watched_store=MagicMock(),
    )
    assert isinstance(mgr.watchlist_store, WatchlistStore)


def test_movie_manager_accepts_injected_watchlist_store():
    custom = MagicMock(spec=WatchlistStore)
    mgr = MovieManager(
        db_config={"host": "x", "user": "y", "password": "z", "db": "w", "port": 3306},
        db_pool=MagicMock(),
        tmdb_helper=MagicMock(),
        candidate_store=MagicMock(),
        projection_store=MagicMock(coordinator=None),
        watched_store=MagicMock(),
        watchlist_store=custom,
    )
    assert mgr.watchlist_store is custom


def test_attach_cache_calls_watchlist_store_attach_cache():
    watchlist_store = MagicMock()
    watchlist_store.attach_cache = MagicMock()
    mgr = MovieManager(
        db_config={"host": "x", "user": "y", "password": "z", "db": "w", "port": 3306},
        db_pool=MagicMock(),
        tmdb_helper=MagicMock(),
        candidate_store=MagicMock(attach_cache=MagicMock()),
        projection_store=MagicMock(coordinator=None),
        watched_store=MagicMock(attach_cache=MagicMock()),
        watchlist_store=watchlist_store,
    )
    cache = MagicMock()
    mgr.attach_cache(cache)
    watchlist_store.attach_cache.assert_called_once_with(cache)


def test_movie_manager_passes_watchlist_store_into_navigator():
    """Discovery exclusion only works if MovieNavigator gets the store —
    not just if MovieManager *holds* it. Patch the navigator constructor
    and assert what it actually received.
    """
    custom_watchlist = MagicMock(spec=WatchlistStore)
    custom_watched = MagicMock()

    with patch(
        "nextreel.application.movie_service.MovieNavigator"
    ) as MockNavigator:
        MovieManager(
            db_config={"host": "x", "user": "y", "password": "z", "db": "w", "port": 3306},
            db_pool=MagicMock(),
            tmdb_helper=MagicMock(),
            candidate_store=MagicMock(),
            projection_store=MagicMock(coordinator=None),
            watched_store=custom_watched,
            watchlist_store=custom_watchlist,
        )

    MockNavigator.assert_called_once()
    kwargs = MockNavigator.call_args.kwargs
    assert kwargs["watchlist_store"] is custom_watchlist
    assert kwargs["watched_store"] is custom_watched
