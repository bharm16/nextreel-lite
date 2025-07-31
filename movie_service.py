import asyncio
import logging
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

# Configure logging for better debugging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s - %(funcName)s - %(levelname)s - %(message)s",
)


class MovieManager:
    def __init__(self, db_config=None, movie_fetcher=None, queue_max_size: int = 20):
        logging.info("Initializing MovieManager")
        self.db_config = db_config or Config.get_db_config()
        self.movie_fetcher = movie_fetcher or ImdbRandomMovieFetcher(
            DatabaseConnectionPool(self.db_config)
        )
        self.movie_queue_manager = MovieQueue(self.db_config, queue_max_size, self.movie_fetcher)
        self.criteria = {}
        # self.future_movies_stack = []
        # self.previous_movies_stack = []
        self.current_displayed_movie = None
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None
        self.tmdb_helper = TMDbHelper(TMDB_API_KEY)  # Initialize TMDbHelper

        self.user_previous_movies_stack = {}  # User-specific previous movies stack
        self.user_future_movies_stack = {}  # User-specific future movies stack
        self.db_config = db_config  # Now db_config is properly defined

    async def start(self):
        # Log the start of the MovieManager
        logging.info("Starting MovieManager")

        # After starting the population task, proceed to set the default backdrop
        await self.set_default_backdrop()
        logging.info("Default backdrop set")

    async def add_user(self, user_id, criteria):
        """
        Add a new user with specific criteria.

        Parameters:
        user_id (str): Unique identifier for the user.
        criteria (dict): Criteria to filter movies for the user.
        """
        logging.info(f"Adding new user with ID: {user_id} and criteria: {criteria}")
        await self.movie_queue_manager.add_user(user_id, criteria)

    async def home(self, user_id):
        logging.info("Accessing home")

        # user_id = await app.get_current_user_id()

        # Check if the movie queue population task is already running
        if not self.movie_queue_manager.is_task_running():
            # If not running, create and start the population task
            self.movie_queue_manager.populate_task = asyncio.create_task(
                self.movie_queue_manager.populate(user_id)
            )
            logging.info("Movie queue population task started")

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
            logging.info("No current movie to display for user_id: {user_id}")
            return None

        # Check if the current movie has a backdrop URL, and if so, render it
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
        logging.info(
            f"Movie skipped due to missing backdrop image for user_id: {user_id}"
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
        movie_instance = Movie(tconst, self.db_config)

        # Fetch movie data
        movie_data = await movie_instance.get_movie_data()
        if not movie_data:
            logging.info(
                f"No data found for movie with tconst: {tconst} and user_id: {user_id}"
            )
            # Optionally, render a 'not found' template or return a simple message
            return "Movie not found", 404

        # Render the template with the fetched movie details
        # Future updates might include user-specific customization based on user_id
        return await render_template(template_name, movie=movie_data)

    def _get_user_stacks(self, user_id):
        start_time = time.time()  # Start timing

        # Initialize stacks for new users
        if user_id not in self.user_previous_movies_stack:
            self.user_previous_movies_stack[user_id] = []
            logging.info(f"Initialized previous movies stack for new user: {user_id}")

        if user_id not in self.user_future_movies_stack:
            self.user_future_movies_stack[user_id] = []
            logging.info(f"Initialized future movies stack for new user: {user_id}")

        # If the stacks already exist, just log that they're being accessed
        else:
            logging.debug(f"Accessing stacks for existing user: {user_id}")

        execution_time = time.time() - start_time
        logging.debug(
            f"_get_user_stacks execution time for user {user_id}: {execution_time:.4f} seconds"
        )

        return (
            self.user_previous_movies_stack[user_id],
            self.user_future_movies_stack[user_id],
        )

    async def get_movie_by_slug(self, user_id, slug):
        """
        Fetch movie details from the user's stacks or queue based on the slug.
        """
        # Retrieve user-specific stacks
        prev_stack, future_stack = self._get_user_stacks(user_id)

        # Check the future stack
        for movie in future_stack:
            if movie.get("slug") == slug:
                return movie

        # Check the current displayed movie
        if (
            self.current_displayed_movie
            and self.current_displayed_movie.get("slug") == slug
        ):
            return self.current_displayed_movie

        # Check the previous stack
        for movie in prev_stack:
            if movie.get("slug") == slug:
                return movie

        # If not found in stacks, optionally check the queue (though this might not be efficient)
        user_queue = await self.movie_queue_manager.get_user_queue(user_id)
        movie_list = list(
            user_queue.queue
        )  # This assumes you can directly access the queue items
        for movie in movie_list:
            if movie.get("slug") == slug:
                return movie

        # If not found, return None
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
            logging.info(f"Pulling movie from movie queue for user_id: {user_id}")
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
            logging.info("No next movie available.")
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
                logging.info("No next movie available.")
                # Redirect to a suitable page or show a message

    async def set_filters(self, user_id):
        logging.info(f"Setting filters for user_id: {user_id}")
        start_time = asyncio.get_event_loop().time()

        # Stop the populate task and signal it to stop immediately using the stop flag
        await self.movie_queue_manager.stop_populate_task(user_id)

        # Now that the task is requested to stop, proceed with emptying the queue
        await self.movie_queue_manager.empty_queue(user_id)

        # Reset the current displayed movie, assuming this needs to be reset for the user
        self.current_displayed_movie = None

        logging.info(
            f"Filters set for user_id: {user_id} in {asyncio.get_event_loop().time() - start_time} seconds"
        )
        return await render_template("set_filters.html")

    async def filtered_movie(self, user_id, form_data):
        logging.info(f"Starting filtering process for user_id: {user_id}")

        # Extract new criteria from form data
        operation_start = time.time()
        new_criteria = extract_movie_filter_criteria(form_data)
        logging.info(
            f"Extracted movie filter criteria for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        # Stop any existing populate task
        operation_start = time.time()
        await self.movie_queue_manager.stop_populate_task(user_id)
        logging.info(
            f"Stopped populate task for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        # Empty the user's queue
        operation_start = time.time()
        await self.movie_queue_manager.empty_queue(user_id)
        logging.info(
            f"Emptied movie queue for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        # Clear stacks and reset seen movies so duplicates are avoided
        prev_stack, future_stack = self._get_user_stacks(user_id)
        prev_stack.clear()
        future_stack.clear()
        await self.movie_queue_manager.reset_seen_movies(user_id)
        self.current_displayed_movie = None

        # Set new criteria for the user
        operation_start = time.time()
        await self.movie_queue_manager.set_criteria(user_id, new_criteria)
        logging.info(
            f"Set new criteria for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        # Reset the stop flag before repopulating
        operation_start = time.time()
        await self.movie_queue_manager.set_stop_flag(user_id, False)
        logging.info(
            f"Reset stop flag for user_id: {user_id} in {time.time() - operation_start:.2f} seconds"
        )

        # Load movies based on the new criteria once
        await self.movie_queue_manager.load_movies_into_queue(user_id)

        # Restart background population task for continuous loading
        await self.movie_queue_manager.start_populate_task(user_id)

        # Fetch and return the next movie for the user
        response = await self.next_movie(user_id)
        if response:
            return response

        # If no movie is available, indicate this to the caller
        return "No movie found", 404


# Main function for testing...
async def main():
    dbconfig = Config.get_db_config()

    movie_manager = MovieManager(dbconfig)
    await movie_manager.start()
    await asyncio.sleep(10)  # Wait for queue to populate

    # Example tconst to test
    test_tconst = "tt0111161"  # Example IMDb ID for "The Shawshank Redemption"

    movie_instance = Movie(
        test_tconst, dbconfig
    )  # Assuming Movie class takes dbconfig as parameter
    movie_data = await movie_instance.get_movie_data()
    if movie_data:
        print(
            f"Successfully fetched movie data for tconst {test_tconst}: {movie_data['title']}"
        )
    else:
        print(f"Failed to fetch movie data for tconst {test_tconst}")


if __name__ == "__main__":
    asyncio.run(main())
