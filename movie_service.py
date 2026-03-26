import asyncio
import httpx

from logging_config import get_logger

from infra.metrics import home_prewarm_failed_total
from infra.navigation_state import normalize_filters
from infra.runtime_schema import ensure_runtime_schema
from movies.candidate_store import CandidateStore
from movies.projection_store import ProjectionStore
from movies.tmdb_client import TMDbHelper
from movie_navigator import MovieNavigator
from movie_renderer import MovieRenderer
from settings import Config, DatabaseConnectionPool

logger = get_logger(__name__)


class MovieManager:
    """Facade coordinating DB-backed navigation, rendering, and enrichment."""

    def __init__(self, db_config=None):
        logger.debug("Initializing MovieManager")
        self.db_config = db_config or Config.get_db_config()
        self.db_pool = DatabaseConnectionPool(self.db_config)
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None
        self.tmdb_helper = TMDbHelper()

        self.candidate_store = CandidateStore(self.db_pool)
        self.projection_store = ProjectionStore(self.db_pool, self.tmdb_helper)
        self.navigation_state_store = None
        self._navigator = None
        self._renderer = MovieRenderer(self.projection_store)

    async def start(self):
        logger.info("Starting MovieManager")
        await self.db_pool.init_pool()
        await ensure_runtime_schema(self.db_pool)

        from infra.navigation_state import NavigationStateStore

        self.navigation_state_store = NavigationStateStore(self.db_pool)
        self._navigator = MovieNavigator(self.candidate_store, self.navigation_state_store)

        try:
            await self.set_default_backdrop()
        except httpx.HTTPError as exc:
            self.default_backdrop_url = None
            logger.warning(
                "TMDb backdrop warm-up failed; continuing without default backdrop: %s",
                exc,
            )

    async def close(self):
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

    async def add_user(self, user_id, criteria):
        """Backward-compatible no-op.

        User bootstrap now happens through NavigationStateStore.
        """
        logger.info("Legacy add_user invoked for %s with criteria %s", user_id, criteria)

    async def home(self, state, legacy_session=None):
        if state and not state.queue and self._navigator:
            try:
                await asyncio.wait_for(
                    self._navigator.prewarm_queue(state.session_id, legacy_session=legacy_session),
                    timeout=0.5,
                )
            except asyncio.TimeoutError:
                home_prewarm_failed_total.inc()
                logger.warning("Home prewarm timed out for %s", state.session_id)
            except asyncio.CancelledError:
                home_prewarm_failed_total.inc()
                logger.warning("Home prewarm cancelled for %s", state.session_id)
            except Exception as exc:
                home_prewarm_failed_total.inc()
                logger.warning("Home prewarm failed for %s: %s", state.session_id, exc)

        return {"default_backdrop_url": self.default_backdrop_url}

    async def set_default_backdrop(self):
        image_data = await self.tmdb_helper.get_images_by_tmdb_id(
            self.default_movie_tmdb_id
        )
        backdrops = image_data["backdrops"]
        if backdrops:
            self.default_backdrop_url = self.tmdb_helper.get_full_image_url(
                backdrops[0]
            )
        else:
            self.default_backdrop_url = None

    async def render_movie_by_tconst(self, state, tconst, template_name="movie.html"):
        previous_count = self._navigator.prev_stack_length(state) if self._navigator and state else 0
        return await self._renderer.render_movie_by_tconst(
            tconst,
            previous_count=previous_count,
            template_name=template_name,
        )

    def get_current_movie_tconst(self, state):
        if not self._navigator or not state:
            return None
        return self._navigator.get_current_movie_tconst(state)

    async def next_movie(self, state, legacy_session=None):
        if not self._navigator or not state:
            return None, None
        return await self._navigator.next_movie(state.session_id, legacy_session=legacy_session)

    async def previous_movie(self, state, legacy_session=None):
        if not self._navigator or not state:
            return None, None
        return await self._navigator.previous_movie(state.session_id, legacy_session=legacy_session)

    async def filtered_movie(self, state, form_data, legacy_session=None):
        if not self._navigator or not state:
            return None, None

        filters = normalize_filters(form_data)
        return await self._navigator.apply_filters(
            state.session_id,
            filters,
            legacy_session=legacy_session,
        )

    async def logout(self, state, legacy_session=None):
        if state and self.navigation_state_store:
            await self.navigation_state_store.delete_state(
                state.session_id,
                legacy_session=legacy_session,
            )
