import asyncio
import logging
from logging_config import get_logger
import time

from quart import render_template, redirect, url_for, session

from settings import Config, DatabaseConnectionPool
from scripts.session_manager import SessionManager
from scripts.movie import Movie
from scripts.filter_backend import (
    ImdbRandomMovieFetcher,
    extract_movie_filter_criteria,
)
from scripts.tmdb_client import TMDbHelper

logger = get_logger(__name__)


class MovieManager:
    def __init__(self, db_config=None):
        logger.debug("Initializing MovieManager")
        self.db_config = db_config or Config.get_db_config()
        self.db_pool = DatabaseConnectionPool(self.db_config)
        self.movie_fetcher = ImdbRandomMovieFetcher(self.db_pool)
        self.queue_size = 20
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None
        self.tmdb_helper = TMDbHelper()  # Initialize TMDbHelper using env key

        self.db_config = db_config  # Now db_config is properly defined
        self.session_manager = SessionManager(self.db_pool)

    async def start(self):
        # Log the start of the MovieManager
        logger.info("Starting MovieManager")

        await self.db_pool.init_pool()
        await self.session_manager.init()

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
        session.setdefault("criteria", criteria)
        session.setdefault("watch_queue", [])
        session.setdefault("previous_movies_stack", [])
        session.setdefault("future_movies_stack", [])
        session.setdefault("seen_tconsts", [])
        await self._load_movies_into_queue()

    async def home(self, user_id):
        logger.debug("Accessing home")

        await self._ensure_queue()

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
        if not current_displayed_movie:
            logger.debug("No current movie to display for user_id: %s", user_id)
            return None

        # Check if the current movie has a backdrop URL, and if so, render it
        if (
            "backdrop_url" in current_displayed_movie
            and current_displayed_movie["backdrop_url"]
        ):
            prev_stack, _ = self._get_user_stacks()
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
        # Initialize a Movie object with the provided tconst
        movie_instance = Movie(tconst, self.db_pool)

        # Fetch movie data
        movie_data = await movie_instance.get_movie_data()
        if not movie_data:
            logger.info(
                f"No data found for movie with tconst: {tconst} and user_id: {user_id}"
            )
            # Optionally, render a 'not found' template or return a simple message
            return "Movie not found", 404

        # Render the template with the fetched movie details
        # Future updates might include user-specific customization based on user_id
        return await render_template(template_name, movie=movie_data)

    def _get_user_stacks(self):
        start_time = time.time()  # Start timing

        prev_stack = session.setdefault("previous_movies_stack", [])
        future_stack = session.setdefault("future_movies_stack", [])

        execution_time = time.time() - start_time
        logger.debug(
            f"_get_user_stacks execution time: {execution_time:.4f} seconds"
        )

        return prev_stack, future_stack

    def _mark_movie_seen(self, tconst):
        seen = set(session.get("seen_tconsts", []))
        if tconst:
            seen.add(tconst)
        session["seen_tconsts"] = list(seen)

    async def _load_movies_into_queue(self):
        queue = session.setdefault("watch_queue", [])
        criteria = session.get("criteria", {})
        limit = self.queue_size - len(queue)
        if limit <= 0:
            return
        rows = await self.movie_fetcher.fetch_random_movies(criteria, limit)
        for row in rows:
            movie = Movie(row["tconst"], self.db_pool)
            movie_data = await movie.get_movie_data()
            if movie_data:
                queue.append(movie_data)
        session["watch_queue"] = queue

    async def _ensure_queue(self):
        queue = session.get("watch_queue", [])
        if not queue:
            await self._load_movies_into_queue()

    async def get_movie_by_slug(self, user_id, slug):
        """
        Fetch movie details from the user's stacks or queue based on the slug.
        """
        # Retrieve stacks stored in session
        prev_stack, future_stack = self._get_user_stacks()

        # Check the future stack
        for movie in future_stack:
            if movie.get("slug") == slug:
                return movie

        # Check the current displayed movie
        current_movie = session.get("current_movie")
        if current_movie and current_movie.get("slug") == slug:
            return current_movie

        # Check the previous stack
        for movie in prev_stack:
            if movie.get("slug") == slug:
                return movie

        # If not found in stacks, check the session watch queue
        for movie in session.get("watch_queue", []):
            if movie.get("slug") == slug:
                return movie

        # If not found, return None
        return None

    async def next_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks()
        queue = session.setdefault("watch_queue", [])

        current_movie = None

        if future_stack:
            current_movie = future_stack.pop()
        elif queue:
            current_movie = queue.pop(0)
        else:
            await self._load_movies_into_queue()
            queue = session.get("watch_queue", [])
            if queue:
                current_movie = queue.pop(0)

        previous = session.get("current_movie")
        if previous and current_movie != previous:
            prev_stack.append(previous)

        session["current_movie"] = current_movie

        if current_movie:
            tconst = current_movie.get("imdb_id")
            self._mark_movie_seen(tconst)
            watch_history = session.setdefault("watch_history", [])
            if tconst and tconst not in watch_history:
                watch_history.append(tconst)
            await self.save_session_to_db(user_id)
            return redirect(url_for("movie_detail", tconst=tconst))
        else:
            logger.info("No next movie available.")

    async def previous_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks()

        current_movie = session.get("current_movie")
        if current_movie:
            future_stack.append(current_movie)

        if prev_stack:
            current_movie = prev_stack.pop()
            session["current_movie"] = current_movie
            tconst = current_movie.get("imdb_id") if current_movie else None
            if tconst:
                session["navigation_back_count"] = session.get("navigation_back_count", 0) + 1
                await self.save_session_to_db(user_id)
                return redirect(url_for("movie_detail", tconst=tconst))
        logger.info("No next movie available.")

    async def set_filters(self, user_id):
        logger.info(f"Setting filters for user_id: {user_id}")

        session["watch_queue"] = []
        session["previous_movies_stack"] = []
        session["future_movies_stack"] = []
        session["seen_tconsts"] = []
        session.pop("current_movie", None)

        return await render_template("set_filters.html")

    async def filtered_movie(self, user_id, form_data):
        logger.info(f"Starting filtering process for user_id: {user_id}")

        new_criteria = extract_movie_filter_criteria(form_data)
        session["criteria"] = new_criteria
        session["watch_queue"] = []
        session["previous_movies_stack"] = []
        session["future_movies_stack"] = []
        session["seen_tconsts"] = []
        session.pop("current_movie", None)

        await self._load_movies_into_queue()

        await self.save_session_to_db(user_id)

        response = await self.next_movie(user_id)
        if response:
            return response

        return "No movie found", 404

    async def save_session_to_db(self, user_id):
        """Persist current session to database."""
        if not user_id:
            return
        data = {
            "created_at": session.get("created_at"),
            "last_active": session.get("last_active"),
            "preferences": session.get("criteria", {}),
            "watch_history": session.get("watch_history", []),
            "favorites": session.get("favorites", []),
            "device_fingerprint": session.get("device_fingerprint"),
            "visit_count": session.get("visit_count", 0),
        }
        await self.session_manager.save_session(user_id, data)


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
