import asyncio
import logging

from quart import render_template

from config import Config
from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from scripts.tmdb_data import TMDbHelper, TMDB_API_KEY

# Configure logging for better debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(filename)s - %(funcName)s - %(levelname)s - %(message)s'
)

class MovieManager:
    def __init__(self, db_config):
        logging.info("Initializing MovieManager")
        self.movie_fetcher = ImdbRandomMovieFetcher(db_config)
        self.criteria = {}
        self.movie_queue = asyncio.Queue(maxsize=20)
        self.movie_queue_manager = MovieQueue(db_config, self.movie_queue)
        # self.future_movies_stack = []
        # self.previous_movies_stack = []
        self.current_displayed_movie = None
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None
        self.tmdb_helper = TMDbHelper(TMDB_API_KEY)  # Initialize TMDbHelper

        self.user_previous_movies_stack = {}  # User-specific previous movies stack
        self.user_future_movies_stack = {}  # User-specific future movies stack

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

    async def home(self,user_id):
        logging.info("Accessing home")

        # user_id = await app.get_current_user_id()

        # Check if the movie queue population task is already running
        if not self.movie_queue_manager.is_task_running():
            # If not running, create and start the population task
            self.movie_queue_manager.populate_task = asyncio.create_task(self.movie_queue_manager.populate(user_id))
            logging.info("Movie queue population task started")

        return await render_template('home.html', default_backdrop_url=self.default_backdrop_url)

    async def set_default_backdrop(self):
        image_data = await self.tmdb_helper.get_images_by_tmdb_id(self.default_movie_tmdb_id)
        backdrops = image_data['backdrops']
        if backdrops:
            self.default_backdrop_url = self.tmdb_helper.get_full_image_url(backdrops[0])
        else:
            self.default_backdrop_url = None

    # async def fetch_and_render_movie(self, template_name='movie.html'):
    # async def fetch_and_render_movie(self, current_displayed_movie, template_name='movie.html'):
    #
    #     # logging.info("Fetching and rendering movie")
    #     if not self.current_displayed_movie:
    #         logging.info("No current movie to display")
    #         return None
    #
    #     # Check if the current movie has a backdrop URL, and if so, render it
    #     if 'backdrop_url' in self.current_displayed_movie and self.current_displayed_movie['backdrop_url']:
    #         return await render_template(template_name,
    #                                      movie=self.current_displayed_movie,
    #                                      previous_count=len(self.user_previous_movies_stack))
    #
    #     # If the movie does not have a backdrop URL, log this and return None
    #     logging.info("Movie skipped due to missing backdrop image")
    #     return None

    async def fetch_and_render_movie(self, current_displayed_movie, user_id, template_name='movie.html'):
        if not current_displayed_movie:
            logging.info("No current movie to display for user_id: {user_id}")
            return None

        # Check if the current movie has a backdrop URL, and if so, render it
        if 'backdrop_url' in current_displayed_movie and current_displayed_movie['backdrop_url']:
            prev_stack, _ = self._get_user_stacks(user_id)
            return await render_template(template_name,
                                         movie=current_displayed_movie,
                                         previous_count=len(prev_stack))

        # If the movie does not have a backdrop URL, log this and return None
        logging.info(f"Movie skipped due to missing backdrop image for user_id: {user_id}")
        return None

    def _get_user_stacks(self, user_id):
        # Initialize stacks for new users
        if user_id not in self.user_previous_movies_stack:
            self.user_previous_movies_stack[user_id] = []
        if user_id not in self.user_future_movies_stack:
            self.user_future_movies_stack[user_id] = []
        return self.user_previous_movies_stack[user_id], self.user_future_movies_stack[user_id]

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
            current_displayed_movie = await user_queue.get()

        # If there is a currently displayed movie, push it to the previous stack
        if current_displayed_movie and self.current_displayed_movie:
            prev_stack.append(self.current_displayed_movie)

        self.current_displayed_movie = current_displayed_movie

        # Render the movie or handle the case where there's no movie to display
        return await self.fetch_and_render_movie(current_displayed_movie, user_id)  # Include user_id here

    # async def previous_movie(self,user_id):
    #     # logging.info("Fetching previous movie")
    #     if self.current_displayed_movie:
    #         self.future_movies_stack.append(self.current_displayed_movie)
    #     if self.previous_movies_stack:
    #         self.current_displayed_movie = self.previous_movies_stack.pop()
    #     else:
    #         self.current_displayed_movie = None
    #
    #     return await self.fetch_and_render_movie()

    async def previous_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks(user_id)

        # If there is a currently displayed movie, push it to the future stack
        if self.current_displayed_movie:
            future_stack.append(self.current_displayed_movie)

        if prev_stack:
            # Pop the last movie from the previous stack and set it as the current displayed movie
            self.current_displayed_movie = prev_stack.pop()
        else:
            # If there are no previous movies, set the current displayed movie to None
            self.current_displayed_movie = None

        # Render the movie or handle the case where there's no movie to display
        return await self.fetch_and_render_movie(self.current_displayed_movie, user_id)

    # async def set_filters(self):
    #     logging.info("Setting filters")
    #     start_time = asyncio.get_event_loop().time()
    #     await self.movie_queue_manager.stop_populate_task()
    #     await self.movie_queue_manager.empty_queue()
    #     self.current_displayed_movie = None
    #     logging.info(f"Filters set in {asyncio.get_event_loop().time() - start_time} seconds")
    #     return await render_template('set_filters.html')

    async def set_filters(self, user_id):
        logging.info(f"Setting filters for user_id: {user_id}")
        start_time = asyncio.get_event_loop().time()

        # Stop the populate task and empty the queue for the specified user
        await self.movie_queue_manager.stop_populate_task(user_id)
        await self.movie_queue_manager.empty_queue(user_id)

        # Reset the current displayed movie, assuming this needs to be reset for the user
        self.current_displayed_movie = None

        logging.info(f"Filters set for user_id: {user_id} in {asyncio.get_event_loop().time() - start_time} seconds")
        return await render_template('set_filters.html')

    async def filtered_movie(self, user_id, form_data):
        logging.info("Filtering movie for user_id: {}".format(user_id))
        new_criteria = extract_movie_filter_criteria(form_data)
        # Update criteria specifically for the user

        # Stop any existing populate task, empty the user's queue, and repopulate based on new criteria
        await self.movie_queue_manager.stop_populate_task(user_id)
        await self.movie_queue_manager.empty_queue(user_id)
        await self.movie_queue_manager.set_criteria(user_id, new_criteria)


        # Start repopulating the queue for the user
        self.movie_queue_manager.populate_task = asyncio.create_task(
            self.movie_queue_manager.populate(user_id)
        )
        logging.info("Criteria updated, repopulating movie queue for user_id: {}".format(user_id))

        # Optionally wait a bit for the queue to start populating
        await asyncio.sleep(5)  # Adjust this based on your needs

        # Now, fetch the next movie for the user from the updated queue
        # It's important to call `next_movie` to update `self.current_displayed_movie` correctly
        await self.next_movie(user_id)

        # Finally, use `self.current_displayed_movie` to render the movie for the user
        # This ensures the movie displayed is the one updated after filtering
        return await self.fetch_and_render_movie(self.current_displayed_movie, user_id)


# Main function for testing...
async def main():
    dbconfig = Config.STACKHERO_DB_CONFIG
    movie_manager = MovieManager(dbconfig)
    await movie_manager.start()
    await asyncio.sleep(10)  # Wait for queue to populate
    # rendered_movie = await movie_manager.fetch_and_render_movie()
    # next_movie_render = await movie_manager.next_movie()
    # prev_movie_render = await movie_manager.previous_movie()


if __name__ == "__main__":
    asyncio.run(main())
