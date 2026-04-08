from __future__ import annotations

import asyncio
from typing import Any, MutableMapping

from filter_contracts import FilterState
from logging_config import get_logger

from infra.metrics import home_prewarm_failed_total
from infra.navigation_state import NavigationState
from infra.pool import DatabaseConnectionPool
from infra.runtime_schema import ensure_runtime_schema
from movies.candidate_store import CandidateStore
from movies.projection_store import ProjectionStore
from movies.tmdb_client import TMDbHelper
from movies.watched_store import WatchedStore
from movie_navigator import MovieNavigator, NavigationOutcome
from movie_renderer import MovieRenderer
from settings import Config

logger = get_logger(__name__)


class MovieManager:
    """Facade coordinating DB-backed navigation, rendering, and enrichment."""

    def __init__(self, db_config: dict[str, Any] | None = None) -> None:
        logger.debug("Initializing MovieManager")
        self.db_config = db_config or Config.get_db_config()
        self.db_pool = DatabaseConnectionPool(self.db_config)
        self.default_movie_tmdb_id: int = 62
        self.default_backdrop_url: str | None = None
        self.tmdb_helper = TMDbHelper()

        self.candidate_store = CandidateStore(self.db_pool)
        self.projection_store = ProjectionStore(self.db_pool, tmdb_helper=self.tmdb_helper)
        self.projection_coordinator = self.projection_store.coordinator
        self.navigation_state_store = None
        self._navigator: MovieNavigator | None = None
        self._renderer = MovieRenderer(self.projection_store)
        self.watched_store = WatchedStore(self.db_pool)

    def attach_cache(self, cache) -> None:
        """Wire a Redis cache into stores that benefit from it.

        Called by ``app.py`` after ``SimpleCacheManager`` initializes so that
        the watched-list hot path can avoid full table scans on every nav.
        """
        if cache is not None:
            self.watched_store._cache = cache

    async def start(self) -> None:
        logger.info("Starting MovieManager")
        await self.db_pool.init_pool()
        await ensure_runtime_schema(self.db_pool)

        from infra.navigation_state import NavigationStateStore

        self.navigation_state_store = NavigationStateStore(self.db_pool)
        self._navigator = MovieNavigator(
            self.candidate_store,
            self.navigation_state_store,
            watched_store=self.watched_store,
        )

    async def close(self) -> None:
        try:
            if self.projection_coordinator:
                await asyncio.wait_for(self.projection_coordinator.aclose(), timeout=5.0)
                logger.info("MovieManager projection enrichment drained")
        except Exception as e:
            logger.error("Error closing projection enrichment: %s", e)

        try:
            if self.tmdb_helper:
                await self.tmdb_helper.close()
                logger.info("MovieManager TMDbHelper closed")
        except Exception as e:
            logger.error("Error closing TMDbHelper: %s", e)

        try:
            if self.db_pool:
                await self.db_pool.close_pool()
                logger.info("MovieManager database pool closed")
        except Exception as e:
            logger.error("Error closing MovieManager: %s", e)

    async def home(
        self,
        state: NavigationState | None,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state and not state.queue and self._navigator:
            try:
                await asyncio.wait_for(
                    self._navigator.prewarm_queue(
                        state.session_id,
                        legacy_session=legacy_session,
                        current_state=state,
                    ),
                    timeout=0.1,
                )
            except Exception as exc:
                home_prewarm_failed_total.inc()
                logger.warning("Home prewarm failed for %s: %s", state.session_id, exc)

        return {"default_backdrop_url": self.default_backdrop_url}

    async def set_default_backdrop(self) -> None:
        image_data = await self.tmdb_helper.get_images_by_tmdb_id(self.default_movie_tmdb_id)
        backdrops = image_data["backdrops"]
        if backdrops:
            self.default_backdrop_url = self.tmdb_helper.get_full_image_url(backdrops[0])
        else:
            self.default_backdrop_url = None

    async def render_movie_by_tconst(
        self,
        state: NavigationState | None,
        tconst: str,
        template_name: str = "movie.html",
    ) -> str | tuple[str, int]:
        previous_count = (
            self._navigator.prev_stack_length(state) if self._navigator and state else 0
        )
        return await self._renderer.render_movie_by_tconst(
            tconst,
            previous_count=previous_count,
            template_name=template_name,
        )

    def get_current_movie_tconst(self, state: NavigationState | None) -> str | None:
        if not self._navigator or not state:
            return None
        return self._navigator.get_current_movie_tconst(state)

    async def next_movie(
        self,
        state: NavigationState | None,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> NavigationOutcome | None:
        if not self._navigator or not state:
            return None
        return await self._navigator.next_movie(
            state.session_id,
            legacy_session=legacy_session,
            current_state=state,
        )

    async def previous_movie(
        self,
        state: NavigationState | None,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> NavigationOutcome | None:
        if not self._navigator or not state:
            return None
        return await self._navigator.previous_movie(
            state.session_id,
            legacy_session=legacy_session,
            current_state=state,
        )

    async def apply_filters(
        self,
        state: NavigationState | None,
        filters: FilterState,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> NavigationOutcome | None:
        if not self._navigator or not state:
            return None

        return await self._navigator.apply_filters(
            state.session_id,
            filters,
            legacy_session=legacy_session,
            current_state=state,
        )

    async def filtered_movie(
        self,
        state: NavigationState | None,
        filters: FilterState,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> NavigationOutcome | None:
        return await self.apply_filters(
            state,
            filters,
            legacy_session=legacy_session,
        )

    async def logout(
        self,
        state: NavigationState | None,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> None:
        if state and self.navigation_state_store:
            await self.navigation_state_store.delete_state(
                state.session_id,
                legacy_session=legacy_session,
            )
