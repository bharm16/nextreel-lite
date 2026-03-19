import asyncio
import logging
from logging_config import get_logger
import time

from quart import render_template, redirect, url_for, session

from settings import Config, DatabaseConnectionPool
from scripts.movie import Movie
from scripts.filter_backend import (
    ImdbRandomMovieFetcher,
    extract_movie_filter_criteria,
)
from scripts.tmdb_client import TMDbHelper
from movie_navigator import MovieNavigator
from movie_renderer import MovieRenderer
from session_keys import (
    CRITERIA_KEY, reset_movie_stacks, init_movie_stacks,
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

        self.db_config = db_config

        # Delegates
        self._navigator = MovieNavigator(self.movie_fetcher, self.db_pool, self.queue_size)
        self._renderer = MovieRenderer(self.db_pool, self.tmdb_helper)

    async def start(self):
        logger.info("Starting MovieManager")
        await self.db_pool.init_pool()
        await self.set_default_backdrop()
        logger.debug("Default backdrop set")

    async def close(self):
        """Close database connections properly"""
        try:
            if self.db_pool:
                await self.db_pool.close_pool()
                logger.info("MovieManager database pool closed")
        except Exception as e:
            logger.error(f"Error closing MovieManager: {e}")

    async def stop(self):
        """Alias for close() method for consistency"""
        await self.close()

    async def add_user(self, user_id, criteria):
        """
        Add a new user with specific criteria.

        Parameters:
        user_id (str): Unique identifier for the user.
        criteria (dict): Criteria to filter movies for the user.
        """
        logger.info(f"Adding new user with ID: {user_id} and criteria: {criteria}")
        init_movie_stacks(criteria)
        await self._load_movies_into_queue()

    async def home(self, user_id):
        logger.debug("Accessing home")
        asyncio.create_task(self._ensure_queue())
        return await render_template(
            "home.html", default_backdrop_url=self.default_backdrop_url
        )

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

    # Expose navigator helpers on the manager for backward compat
    def _get_user_stacks(self):
        return self._navigator._get_user_stacks()

    def _mark_movie_seen(self, tconst):
        return self._navigator._mark_movie_seen(tconst)

    async def _load_movies_into_queue(self):
        return await self._navigator._load_movies_into_queue()

    async def _ensure_queue(self):
        return await self._navigator._ensure_queue()

    def get_current_movie_tconst(self):
        return self._navigator.get_current_movie_tconst()

    async def get_movie_by_slug(self, user_id, slug):
        return await self._navigator.get_movie_by_slug(user_id, slug)

    async def next_movie(self, user_id):
        return await self._navigator.next_movie(user_id)

    async def previous_movie(self, user_id):
        return await self._navigator.previous_movie(user_id)

    async def set_filters(self, user_id):
        logger.info(f"Setting filters for user_id: {user_id}")
        reset_movie_stacks()
        return await render_template("set_filters.html")

    async def filtered_movie(self, user_id, form_data):
        logger.info(f"Starting filtering process for user_id: {user_id}")

        new_criteria = extract_movie_filter_criteria(form_data)
        session[CRITERIA_KEY] = new_criteria
        reset_movie_stacks()

        await self._load_movies_into_queue()

        response = await self.next_movie(user_id)
        if response:
            return response

        return "No movie found", 404


# Main function for testing...
async def main():
    dbconfig = Config.get_db_config()

    movie_manager = MovieManager(dbconfig)
    await movie_manager.start()
    await asyncio.sleep(10)  # Wait for queue to populate

    # Example tconst to test
    test_tconst = "tt0111161"  # Example IMDb ID for "The Shawshank Redemption"

    movie_instance = Movie(test_tconst, movie_manager.db_pool)
    movie_data = await movie_instance.get_movie_data()
    if movie_data:
        print(
            f"Successfully fetched movie data for tconst {test_tconst}: {movie_data['title']}"
        )
    else:
        print(f"Failed to fetch movie data for tconst {test_tconst}")


if __name__ == "__main__":
    asyncio.run(main())
