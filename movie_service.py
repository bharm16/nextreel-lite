from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, MutableMapping


def _nav_dual_write_active() -> bool:
    """Read the dual-write flag from the environment, once per call.

    The home-prewarm decision reads this exactly once at schedule time and
    passes the resulting boolean into the background task — never inside
    the task, which would re-read stale config.
    """
    raw = os.getenv("NAV_STATE_DUAL_WRITE_ENABLED", "true").strip().lower()
    return raw not in ("false", "0", "no", "off")

from filter_contracts import FilterState
from logging_config import get_logger

from infra.metrics import home_prewarm_failed_total
from infra.navigation_state import NavigationState, NavigationStateStore
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
        # Background-task scheduler hook wired from ``app.create_app()``. When
        # present, request handlers can schedule best-effort work (queue
        # prewarm, local enrichment prefetch) off the request path.
        self._background_scheduler: (
            Callable[[Awaitable[Any]], Any] | None
        ) = None

    def attach_background_scheduler(
        self,
        scheduler: Callable[[Awaitable[Any]], Any] | None,
    ) -> None:
        """Wire an app-owned background task scheduler.

        The scheduler is called with a single coroutine argument and is
        expected to wrap it in ``asyncio.create_task`` and register the task
        in ``app.background_tasks`` so it is awaited on shutdown.
        """
        self._background_scheduler = scheduler

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
        if state and not state.queue and self._navigator:
            # Read the dual-write flag exactly once here — the background
            # task must not re-read stale config mid-flight.
            dual_write_active = _nav_dual_write_active()

            if self._background_scheduler is not None and not dual_write_active:
                # Capture only plain data. The background task must not
                # touch request/session/g — it receives the session_id
                # (a plain str) and schedules prewarm without the legacy
                # session mapping, since dual-write is off by assumption.
                session_id = state.session_id
                navigator = self._navigator

                async def _bg_prewarm() -> None:
                    try:
                        await navigator.prewarm_queue(
                            session_id,
                            legacy_session=None,
                            current_state=None,
                        )
                    except Exception as exc:
                        home_prewarm_failed_total.inc()
                        logger.warning(
                            "Background home prewarm failed for %s: %s",
                            session_id,
                            exc,
                        )

                self.schedule_background(_bg_prewarm())
            else:
                try:
                    prewarm_timeout = float(os.getenv("PREWARM_TIMEOUT_SECONDS", "0.1"))
                except ValueError:
                    prewarm_timeout = 0.1
                try:
                    await asyncio.wait_for(
                        self._navigator.prewarm_queue(
                            state.session_id,
                            legacy_session=legacy_session,
                            current_state=state,
                        ),
                        timeout=prewarm_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.info(
                        "prewarm_queue exceeded %.2fs timeout; skipping prewarm",
                        prewarm_timeout,
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
