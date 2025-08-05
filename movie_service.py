"""High level service for coordinating movie retrieval and rendering.

This module exposes :class:`MovieManager`, an orchestration layer that ties
together the queueing system, database access layer and the TMDb API client.
The goal is to abstract all of the plumbing involved in serving movies to the
web application so that the Quart routes can remain relatively thin.

Only comments and docstrings are added in this commit – the runtime behaviour
remains unchanged.
"""

import asyncio
import logging
from logging_config import get_logger
import time

from quart import render_template, redirect, url_for

from settings import Config, DatabaseConnectionPool
from scripts.movie import Movie
from scripts.movie_queue import MovieQueue
from scripts.filter_backend import (
    ImdbRandomMovieFetcher,
    extract_movie_filter_criteria,
)
from scripts.tmdb_client import TMDbHelper, TMDB_API_KEY

# Create a module level logger that will inherit configuration from
# ``logging_config.setup_logging``.  Using ``__name__`` ensures the logger is
# namespaced to this module which makes filtering in logs easier.
logger = get_logger(__name__)


class MovieManager:
    """Coordinate user movie queues and rendering logic.

    Instances of ``MovieManager`` maintain per-user queues of movies, handle
    fetching additional information from TMDb and finally render the resulting
    data using Jinja templates.  The class is intentionally stateful – it keeps
    track of movies a user has already seen so that navigating "next" and
    "previous" works as expected.
    """

    def __init__(self, db_config=None):
        logger.debug("Initializing MovieManager")

        # Database configuration is either provided explicitly or pulled from the
        # application settings.  The configuration is used to create a connection
        # pool for subsequent queries.
        self.db_config = db_config or Config.get_db_config()
        self.db_pool = DatabaseConnectionPool(self.db_config)

        # ``ImdbRandomMovieFetcher`` encapsulates SQL required to pull random
        # movies that match a set of criteria.  ``MovieQueue`` then uses that
        # fetcher to keep an ``asyncio.Queue`` populated for each user.
        self.movie_fetcher = ImdbRandomMovieFetcher(self.db_pool)
        self.movie_queue_manager = MovieQueue(
            self.db_pool, self.movie_fetcher, queue_size=20
        )

        # Default filter criteria; these can later be customised per user.
        self.criteria = {}

        # Currently displayed movie and default imagery used on the home page.
        self.current_displayed_movie = None
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None

        # Helper for talking to the TMDb API.  The API key is loaded from
        # settings and simply forwarded here.
        self.tmdb_helper = TMDbHelper(TMDB_API_KEY)

        # Per-user stacks used to implement the "back" and "forward" movie
        # navigation behaviour.  Each user gets their own list so that
        # operations for one user do not interfere with another.
        self.user_previous_movies_stack = {}
        self.user_future_movies_stack = {}

        # Store the configuration argument explicitly; useful for debugging.
        self.db_config = db_config

    async def start(self):
        # Log the start of the MovieManager
        logger.info("Starting MovieManager")

        await self.db_pool.init_pool()

        # After starting the population task, proceed to set the default backdrop
        await self.set_default_backdrop()
        logger.debug("Default backdrop set")

    async def add_user(self, user_id, criteria):
        """
        Add a new user with specific criteria.

        Parameters:
        user_id (str): Unique identifier for the user.
        criteria (dict): Criteria to filter movies for the user.
        """
        logger.info(f"Adding new user with ID: {user_id} and criteria: {criteria}")
        await self.movie_queue_manager.add_user(user_id, criteria)

    async def home(self, user_id):
        logger.debug("Accessing home")

        # user_id = await app.get_current_user_id()

        # Check if the movie queue population task is already running
        if not self.movie_queue_manager.is_task_running():
            # If not running, create and start the population task
            self.movie_queue_manager.populate_task = asyncio.create_task(
                self.movie_queue_manager.populate(user_id)
            )
            logger.info("Movie queue population task started")

        return await render_template(
            "home.html", default_backdrop_url=self.default_backdrop_url
        )

    async def set_default_backdrop(self):
        """Fetch and cache a default backdrop image.

        The application home page displays a static background image.  Rather
        than hard-coding a URL, we lazily fetch a backdrop for a known movie
        (``self.default_movie_tmdb_id``) from TMDb and cache the resulting
        absolute URL for later use.
        """

        image_data = await self.tmdb_helper.get_images_by_tmdb_id(
            self.default_movie_tmdb_id
        )
        backdrops = image_data["backdrops"]
        if backdrops:
            # Take the first available backdrop and convert it into a fully
            # qualified image URL.
            self.default_backdrop_url = self.tmdb_helper.get_full_image_url(
                backdrops[0]
            )
        else:
            self.default_backdrop_url = None

    async def fetch_and_render_movie(
        self, current_displayed_movie, user_id, template_name="movie.html"
    ):
        """Render a movie template if a valid movie object is supplied.

        The method ensures that the movie has a backdrop image before attempting
        to render.  If the image is missing we skip rendering entirely – the UI
        relies heavily on imagery and a missing backdrop would look awkward.
        ``None`` is returned in that case so the caller can take appropriate
        action.
        """

        if not current_displayed_movie:
            logger.debug("No current movie to display for user_id: %s", user_id)
            return None

        # Only render the movie if a backdrop image is available.  The template
        # also displays how many movies are in the user's "previous" stack so
        # that navigation buttons can be enabled/disabled accordingly.
        if (
            "backdrop_url" in current_displayed_movie
            and current_displayed_movie["backdrop_url"]
        ):
            prev_stack, _ = self._get_user_stacks(user_id)
            return await render_template(
                template_name,
                movie=current_displayed_movie,
                previous_count=len(prev_stack),
            )

        # If the movie does not have a backdrop URL, log this and return None
        logger.debug(
            "Movie skipped due to missing backdrop image for user_id: %s", user_id
        )
        return None

    async def render_movie_by_tconst(self, user_id, tconst, template_name="movie.html"):
        """
        Fetch movie details using a tconst and render the movie, potentially using user_id
        for user-specific logic in the future.

        Parameters:
        - user_id (str): The ID of the user requesting the movie.
        - tconst (str): The IMDb ID of the movie.
        - template_name (str): The template name for rendering the movie details.
        """
        # Initialize a Movie object which encapsulates fetching all metadata for
        # the given ``tconst``.  The heavy lifting is performed inside the
        # ``Movie`` class; this method merely coordinates the asynchronous call
        # and renders the result.
        movie_instance = Movie(tconst, self.db_pool)

        # Fetch movie data from TMDb and the local database.  If nothing is
        # returned we respond with a simple 404.
        movie_data = await movie_instance.get_movie_data()
        if not movie_data:
            logger.info(
                f"No data found for movie with tconst: {tconst} and user_id: {user_id}"
            )
            return "Movie not found", 404

        # Render the template with the fetched movie details.  At present the
        # ``user_id`` is unused but is accepted so future personalisation can be
        # implemented without changing the function signature.
        return await render_template(template_name, movie=movie_data)

    def _get_user_stacks(self, user_id):
        """Return (creating if necessary) the navigation stacks for ``user_id``."""

        start_time = time.time()  # Start timing

        # The navigation stacks are lazily created the first time a user is seen.
        # This keeps memory usage proportional to the number of active users.
        if user_id not in self.user_previous_movies_stack:
            self.user_previous_movies_stack[user_id] = []
            logger.info(f"Initialized previous movies stack for new user: {user_id}")

        if user_id not in self.user_future_movies_stack:
            self.user_future_movies_stack[user_id] = []
            logger.info(f"Initialized future movies stack for new user: {user_id}")
        else:
            logger.debug(f"Accessing stacks for existing user: {user_id}")

        execution_time = time.time() - start_time
        logger.debug(
            f"_get_user_stacks execution time for user {user_id}: {execution_time:.4f} seconds"
        )

        return (
            self.user_previous_movies_stack[user_id],
            self.user_future_movies_stack[user_id],
        )

    async def get_movie_by_slug(self, user_id, slug):
        """Search all user owned collections for a movie matching ``slug``."""

        # Retrieve user-specific stacks for inspection.
        prev_stack, future_stack = self._get_user_stacks(user_id)

        # Look ahead in the "future" stack first as those are movies the user
        # may navigate to next.
        for movie in future_stack:
            if movie.get("slug") == slug:
                return movie

        # Then check the currently displayed movie.
        if (
            self.current_displayed_movie
            and self.current_displayed_movie.get("slug") == slug
        ):
            return self.current_displayed_movie

        # Finally search through the history stack.
        for movie in prev_stack:
            if movie.get("slug") == slug:
                return movie

        # As a last resort inspect the queued movies.  Accessing the queue's
        # internal list is not ideal but works for educational purposes here.
        user_queue = await self.movie_queue_manager.get_user_queue(user_id)
        movie_list = list(user_queue.queue)
        for movie in movie_list:
            if movie.get("slug") == slug:
                return movie

        # If no matching movie was found return ``None`` so callers can handle
        # the absence appropriately.
        return None

    async def next_movie(self, user_id):
        # Retrieve user-specific queues and stacks
        user_queue = await self.movie_queue_manager.get_user_queue(user_id)
        prev_stack, future_stack = self._get_user_stacks(user_id)

        current_displayed_movie = None

        # Check and handle the next movie from the user's future stack or queue
        if future_stack:
            current_displayed_movie = future_stack.pop()
        elif not user_queue.empty():
            logger.debug("Pulling movie from movie queue for user_id: %s", user_id)
            current_displayed_movie = await self.movie_queue_manager.dequeue_movie(user_id)

        # If there is a currently displayed movie, push it to the previous stack
        if (
            self.current_displayed_movie
            and current_displayed_movie != self.current_displayed_movie
        ):
            prev_stack.append(self.current_displayed_movie)

        self.current_displayed_movie = current_displayed_movie

        # Extract the IMDb ID from the current displayed movie
        tconst = (
            current_displayed_movie.get("imdb_id") if current_displayed_movie else None
        )

        # If a tconst is available, call render_movie_by_tconst with the necessary parameters
        if tconst:
            await self.movie_queue_manager.mark_movie_seen(user_id, tconst)
            # Assuming 'movie_detail.html' is the template where you want to display the movie details
            return redirect(url_for("movie_detail", tconst=tconst))
        else:
            # Handle the case where there's no next movie, adjust the logic as needed
            logger.info("No next movie available.")
            # Redirect to a suitable page or show a message

    async def previous_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks(user_id)

        # If there is a currently displayed movie, push it to the future stack
        if self.current_displayed_movie:
            future_stack.append(self.current_displayed_movie)

        if prev_stack:
            # Pop the last movie from the previous stack and set it as the current displayed movie
            self.current_displayed_movie = prev_stack.pop()
            # Extract the IMDb ID from the current displayed movie
            tconst = (
                self.current_displayed_movie.get("imdb_id")
                if self.current_displayed_movie
                else None
            )

            # If a tconst is available, call render_movie_by_tconst with the necessary parameters
            if tconst:
                # Assuming 'movie_detail.html' is the template where you want to display the movie details
                return redirect(url_for("movie_detail", tconst=tconst))
            else:
                # Handle the case where there's no next movie, adjust the logic as needed
                logger.info("No next movie available.")
                # Redirect to a suitable page or show a message

    async def set_filters(self, user_id):
        """Render filter selection page after resetting the user's queue."""

        logger.info(f"Setting filters for user_id: {user_id}")
        start_time = asyncio.get_event_loop().time()

        # Stop the background population task and drain any queued movies so
        # that the new filter criteria start with a clean slate.
        await self.movie_queue_manager.stop_populate_task(user_id)
        await self.movie_queue_manager.empty_queue(user_id)

        # Remove reference to the current movie so navigation restarts once new
        # criteria are applied.
        self.current_displayed_movie = None

        logger.info(
            f"Filters set for user_id: {user_id} in {asyncio.get_event_loop().time() - start_time} seconds"
        )
        return await render_template("set_filters.html")

    async def filtered_movie(self, user_id, form_data):
        """Process submitted filter form and return a filtered movie."""

        logger.info(f"Starting filtering process for user_id: {user_id}")

        # Pull the new filter criteria from the submitted form and log how long
        # that extraction took.
        operation_start = time.time()
        new_criteria = extract_movie_filter_criteria(form_data)
        logger.info(
            f"Extracted movie filter criteria for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        # Stop any existing queue population and drain old movies.
        operation_start = time.time()
        await self.movie_queue_manager.stop_populate_task(user_id)
        logger.info(
            f"Stopped populate task for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        operation_start = time.time()
        await self.movie_queue_manager.empty_queue(user_id)
        logger.info(
            f"Emptied movie queue for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        # Clear navigation stacks and reset the set of movies already shown to
        # the user.  This prevents duplicates when the new criteria overlap with
        # previously seen movies.
        prev_stack, future_stack = self._get_user_stacks(user_id)
        prev_stack.clear()
        future_stack.clear()
        await self.movie_queue_manager.reset_seen_movies(user_id)
        self.current_displayed_movie = None

        # Persist the new criteria and allow the populate task to run again.
        operation_start = time.time()
        await self.movie_queue_manager.set_criteria(user_id, new_criteria)
        logger.info(
            f"Set new criteria for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        operation_start = time.time()
        await self.movie_queue_manager.set_stop_flag(user_id, False)
        logger.info(
            f"Reset stop flag for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        # Prime the queue with movies matching the new criteria and restart the
        # background population task.
        await self.movie_queue_manager.load_movies_into_queue(user_id)
        await self.movie_queue_manager.start_populate_task(user_id)

        # Immediately display the next movie to provide feedback to the user.
        response = await self.next_movie(user_id)
        if response:
            return response

        return "No movie found", 404


async def main():
    """Simple manual test harness for the module.

    Running this file directly will fetch metadata for a hard-coded movie and
    print the result to stdout.  It is primarily useful during development to
    verify that database connectivity and TMDb integration still work.
    """

    dbconfig = Config.get_db_config()
    movie_manager = MovieManager(dbconfig)
    await movie_manager.start()
    await asyncio.sleep(10)  # Wait for queue to populate

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
