from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, MutableMapping


from nextreel.domain.filter_contracts import FilterState
from logging_config import get_logger

from nextreel.application.navigation_state_service import NavigationStateStore
from nextreel.domain.navigation_state import NavigationState
from infra.pool import DatabaseConnectionPool
from infra.runtime_schema import ensure_runtime_schema
from movies.candidate_store import CandidateStore
from movies.projection_store import ProjectionStore
from movies.tmdb_client import TMDbHelper
from movies.watched_store import WatchedStore
from nextreel.application.home_prewarm_service import HomePrewarmService
from nextreel.application.movie_navigator import MovieNavigator, NavigationOutcome
from nextreel.web.movie_renderer import MovieRenderer
from settings import Config

logger = get_logger(__name__)


class MovieManager:
    """Facade coordinating DB-backed navigation, rendering, and enrichment."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        *,
        db_pool: DatabaseConnectionPool | None = None,
        tmdb_helper: TMDbHelper | None = None,
        candidate_store: CandidateStore | None = None,
        projection_store: ProjectionStore | None = None,
        watched_store: WatchedStore | None = None,
        renderer: MovieRenderer | None = None,
        navigation_state_store: NavigationStateStore | None = None,
        navigator: MovieNavigator | None = None,
        home_prewarm_service: HomePrewarmService | None = None,
    ) -> None:
        logger.debug("Initializing MovieManager")
        self.db_config = db_config or Config.get_db_config()
        self.db_pool = db_pool or DatabaseConnectionPool(self.db_config)
        self.default_movie_tmdb_id: int = 62
        self.default_backdrop_url: str | None = None
        self.tmdb_helper = tmdb_helper or TMDbHelper()

        self.candidate_store = candidate_store or CandidateStore(self.db_pool)
        self.projection_store = projection_store or ProjectionStore(
            self.db_pool,
            tmdb_helper=self.tmdb_helper,
        )
        self.projection_coordinator = self.projection_store.coordinator
        self.navigation_state_store = navigation_state_store
        self._navigator = navigator
        self._renderer = renderer or MovieRenderer(self.projection_store)
        self.watched_store = watched_store or WatchedStore(self.db_pool)
        self._home_prewarm_service = home_prewarm_service or HomePrewarmService()
        # Background-task scheduler hook wired from ``app.create_app()``. When
        # present, request handlers can schedule best-effort work (queue
        # prewarm, local enrichment prefetch) off the request path.
        self._background_scheduler: Callable[[Awaitable[Any]], Any] | None = None

    def attach_background_scheduler(
        self,
        scheduler: Callable[[Awaitable[Any]], Any] | None,
    ) -> None:
        """Wire an app-owned background task scheduler.

        The scheduler is called with a single coroutine argument and is
        expected to wrap it in ``asyncio.create_task`` and register the task
        in ``app.background_tasks`` so it is awaited on shutdown.

        Propagates the same scheduler into the projection enrichment
        coordinator so its in-flight prefetch tasks share the same drain
        set — callers should not also wire the coordinator separately.
        """
        self._background_scheduler = scheduler
        if self.projection_coordinator is not None:
            self.projection_coordinator.attach_background_scheduler(scheduler)

    def schedule_background(self, coro: Awaitable[Any]) -> bool:
        """Hand a coroutine to the app scheduler, closing it on failure."""
        scheduler = self._background_scheduler
        if scheduler is None:
            coro.close()
            return False
        try:
            scheduler(coro)
            return True
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Background scheduler rejected task: %s", exc)
            try:
                coro.close()
            except Exception:
                pass
            return False

    def attach_cache(self, cache) -> None:
        """Wire a Redis cache into stores that benefit from it.

        Called by ``app.py`` after ``SimpleCacheManager`` initializes so that
        the watched-list hot path can avoid full table scans on every nav,
        the query builder can single-flight COUNT(*) across workers, the
        enrichment coordinator can dedup enqueue-in-flight across workers,
        and the candidate store can share filter-result snapshots.
        """
        if cache is None:
            return
        self.watched_store.attach_cache(cache)
        if self.projection_coordinator is not None:
            self.projection_coordinator.attach_cache(cache)
        if self.candidate_store is not None:
            self.candidate_store.attach_cache(cache)
        if self.navigation_state_store is not None:
            self.navigation_state_store.attach_cache(cache)

    async def start(self) -> None:
        logger.info("Starting MovieManager")
        await self.db_pool.init_pool()
        await ensure_runtime_schema(self.db_pool)

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

    def prev_stack_length(self, state: NavigationState | None) -> int:
        """Number of entries currently in the prev stack for this navigation state.

        Facade over the underlying navigator so callers don't reach into
        ``_navigator`` directly.
        """
        if self._navigator is None or state is None:
            return 0
        return self._navigator.prev_stack_length(state)

    async def home(
        self,
        state: NavigationState | None,
        legacy_session: MutableMapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        prewarm_service = getattr(self, "_home_prewarm_service", None)
        if prewarm_service is None:
            prewarm_service = HomePrewarmService()
            self._home_prewarm_service = prewarm_service

        await prewarm_service.prewarm(
            state=state,
            navigator=self._navigator,
            legacy_session=legacy_session,
            background_scheduler=self._background_scheduler,
            schedule_background=self.schedule_background,
        )

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
        current_state = state
        if not state.queue and self._home_prewarm_service is not None:
            waited = await self._home_prewarm_service.wait_for_session(state.session_id)
            if waited:
                current_state = None
        return await self._navigator.next_movie(
            state.session_id,
            legacy_session=legacy_session,
            current_state=current_state,
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
