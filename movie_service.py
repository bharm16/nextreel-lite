import httpx
from logging_config import get_logger

from quart import session

from settings import Config, DatabaseConnectionPool
from movies.query_builder import ImdbRandomMovieFetcher
from movies.filter_parser import extract_movie_filter_criteria
from movies.tmdb_client import TMDbHelper
from movie_navigator import MovieNavigator
from movie_renderer import MovieRenderer
from session.keys import (
    CRITERIA_KEY,
    init_movie_stacks,
    reset_movie_stacks,
)

logger = get_logger(__name__)


class MovieManager:
    """Facade that coordinates movie navigation, rendering, and queue management.

    Delegates to ``MovieNavigator`` for session stack operations and to
    ``MovieRenderer`` for template rendering.  All public methods are preserved
    for backward compatibility.
    """

    def __init__(self, db_config=None):
        logger.debug("Initializing MovieManager")
        self.db_config = db_config or Config.get_db_config()
        self.db_pool = DatabaseConnectionPool(self.db_config)
        self.movie_fetcher = ImdbRandomMovieFetcher(self.db_pool)
        self.queue_size = 2  # Reduced from 5 for instant initial loading
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None
        self.tmdb_helper = TMDbHelper()

        # Delegates — share the single TMDbHelper (and its httpx pool)
        self._navigator = MovieNavigator(
            self.movie_fetcher, self.db_pool, self.queue_size,
            tmdb_helper=self.tmdb_helper,
        )
        self._renderer = MovieRenderer(self.db_pool, self.tmdb_helper)

    async def start(self):
        logger.info("Starting MovieManager")
        await self.db_pool.init_pool()
        try:
            await self.set_default_backdrop()
        except httpx.HTTPError as exc:
            self.default_backdrop_url = None
            logger.warning(
                "TMDb backdrop warm-up failed; continuing without default backdrop: %s",
                exc,
            )
        logger.debug("Default backdrop set")

    async def close(self):
        """Close database connections and HTTP clients properly"""
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
        """
        Add a new user with specific criteria.

        Parameters:
        user_id (str): Unique identifier for the user.
        criteria (dict): Criteria to filter movies for the user.
        """
        logger.info("Adding new user with ID: %s and criteria: %s", user_id, criteria)
        init_movie_stacks(criteria)
        await self._navigator.load_initial_queue()

    async def home(self, user_id):
        """Return home page data."""
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

    async def fetch_and_render_movie(
        self, current_displayed_movie, user_id, template_name="movie.html"
    ):
        return await self._renderer.fetch_and_render_movie(
            current_displayed_movie, user_id, self._navigator.prev_stack_length(), template_name
        )

    async def render_movie_by_tconst(self, user_id, tconst, template_name="movie.html"):
        return await self._renderer.render_movie_by_tconst(user_id, tconst, template_name)

    # Public navigation helpers delegated to MovieNavigator.
    # NOTE: The previous pass-through private methods (_get_user_stacks,
    # _mark_movie_seen, _load_movies_into_queue, _ensure_queue) were removed
    # because exposing private APIs through a facade defeats its purpose.
    # Internal callers should use the navigator directly via self._navigator.

    def get_current_movie_tconst(self):
        return self._navigator.get_current_movie_tconst()

    async def next_movie(self, user_id):
        return await self._navigator.next_movie(user_id)

    async def previous_movie(self, user_id):
        return await self._navigator.previous_movie(user_id)

    async def filtered_movie(self, user_id, form_data):
        logger.info("Starting filtering process for user_id: %s", user_id)

        new_criteria = extract_movie_filter_criteria(form_data)
        session[CRITERIA_KEY] = new_criteria
        reset_movie_stacks()

        await self._navigator.load_initial_queue()

        response = await self.next_movie(user_id)
        if response:
            return response

        return "No movie found", 404
