import asyncio
from logging_config import get_logger

from quart import copy_current_request_context, has_request_context, session

from settings import Config, DatabaseConnectionPool
from scripts.filter_backend import (
    ImdbRandomMovieFetcher,
    extract_movie_filter_criteria,
)
from scripts.tmdb_client import TMDbHelper
from movie_navigator import MovieNavigator
from movie_renderer import MovieRenderer
from session_keys import (
    CRITERIA_KEY,
    USER_ID_KEY,
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
        self._queue_prefetch_tasks: dict[str, asyncio.Task] = {}
        self._queue_prefetch_lock = asyncio.Lock()

    async def start(self):
        logger.info("Starting MovieManager")
        await self.db_pool.init_pool()
        await self.set_default_backdrop()
        logger.debug("Default backdrop set")

    async def close(self):
        """Close database connections and HTTP clients properly"""
        await self._cancel_prefetch_tasks()

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
        """Return home page data. Kicks off background queue prefetch."""
        logger.debug("Accessing home")
        await self._start_queue_prefetch(user_id)
        return {"default_backdrop_url": self.default_backdrop_url}

    def _queue_task_key(self, user_id: str | None) -> str:
        if has_request_context():
            return session.get(USER_ID_KEY) or user_id or "anonymous"
        return user_id or "anonymous"

    async def _start_queue_prefetch(self, user_id: str | None) -> asyncio.Task:
        """Ensure only one background queue-population task runs per user."""
        task_key = self._queue_task_key(user_id)

        async with self._queue_prefetch_lock:
            existing = self._queue_prefetch_tasks.get(task_key)
            if existing and not existing.done():
                return existing

            if has_request_context():
                @copy_current_request_context
                async def populate_queue():
                    await self._navigator._ensure_queue()
            else:
                async def populate_queue():
                    await self._navigator._ensure_queue()

            task = asyncio.create_task(populate_queue(), name=f"queue-prefetch:{task_key}")
            task.add_done_callback(
                lambda done_task, key=task_key: self._handle_prefetch_task_done(
                    key, done_task
                )
            )
            self._queue_prefetch_tasks[task_key] = task
            return task

    def _handle_prefetch_task_done(self, task_key: str, task: asyncio.Task) -> None:
        """Log failures once and evict completed tasks from the registry."""
        self._queue_prefetch_tasks.pop(task_key, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("Queue prefetch task failed for %s: %s", task_key, exc, exc_info=exc)

    async def _cancel_prefetch_tasks(self) -> None:
        """Cancel any outstanding queue-prefetch tasks during shutdown."""
        async with self._queue_prefetch_lock:
            tasks = list(self._queue_prefetch_tasks.values())
            self._queue_prefetch_tasks.clear()

        if not tasks:
            return

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

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
        prev_stack, _ = self._navigator._get_user_stacks()
        return await self._renderer.fetch_and_render_movie(
            current_displayed_movie, user_id, len(prev_stack), template_name
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
