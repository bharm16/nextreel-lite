import asyncio
import logging
import httpx
import random
from quart import render_template
from config import Config
from scripts.movie import Movie
from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from scripts.tmdb_data import TMDbHelper, TMDB_API_KEY

# Configure logging for better debugging
logging.basicConfig(level=logging.INFO)


class MovieManager:
    def __init__(self, db_config, user_id=None):  # Add user_id as a parameter
        logging.info("Initializing MovieManager")
        self.movie_fetcher = ImdbRandomMovieFetcher(db_config)
        self.criteria = {}
        self.movie_queue = asyncio.Queue(maxsize=20)
        self.movie_queue_manager = MovieQueue(db_config, self.movie_queue)
        self.future_movies_stack = []
        self.previous_movies_stack = []
        self.current_displayed_movie = None
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None
        self.tmdb_helper = TMDbHelper(TMDB_API_KEY)  # Initialize TMDbHelper
        self.user_queues = {}  # Dictionary to store movie queues per user
        self.user_id = user_id  # Store the user_id
        self.movie_queues = {}  # Dictionary to store MovieQueue instances per user
        self.db_config = db_config  # Ensure this attribute is initialized

    async def get_user_movie_queue(self, user_id):
        if user_id not in self.movie_queues:
            queue = asyncio.Queue(maxsize=20)
            self.movie_queues[user_id] = MovieQueue(self.db_config, queue)
        return self.movie_queues[user_id]

    async def start_population_task(self, user_id):
        movie_queue = await self.get_user_movie_queue(user_id)
        if not movie_queue.is_task_running(user_id):  # Pass user_id to is_task_running
            movie_queue.populate_task = asyncio.create_task(movie_queue.populate(user_id))

    # async def start_population_task(self):
    #     logging.info("Starting population task")
    #     if not self.movie_queue_manager.is_task_running():
    #         self.movie_queue_manager.populate_task = asyncio.create_task(self.movie_queue_manager.populate())

    async def set_default_backdrop(self):
        image_data = await self.tmdb_helper.get_images_by_tmdb_id(self.default_movie_tmdb_id)
        backdrops = image_data['backdrops']
        if backdrops:
            self.default_backdrop_url = self.tmdb_helper.get_full_image_url(backdrops[0])
        else:
            self.default_backdrop_url = None

    async def start_for_user(self, user_id):
        logging.info("Starting MovieManager for user: {}".format(user_id))
        await self.start_population_task(user_id)  # Start populating the queue for a specific user
        await self.set_default_backdrop()  # This remains the same for all users

    # async def start(self):
    #     logging.info("Starting MovieManager")
    #     await self.movie_queue_manager.populate()  # Start populating the queue
    #     await self.set_default_backdrop()

    async def fetch_and_render_movie(self, user_id, template_name='movie.html'):
        user_queue = await self.get_user_queue(user_id)
        if user_queue.empty():
            logging.info("No current movie to display for user_id: {}".format(user_id))
            return None

        current_displayed_movie = await user_queue.get()  # Get the current movie for the user

        # Check if the current movie has a backdrop URL, and if so, render it
        if 'backdrop_url' in current_displayed_movie and current_displayed_movie['backdrop_url']:
            return await render_template(template_name,
                                         movie=current_displayed_movie,
                                         previous_count=len(self.previous_movies_stack))

        # If the movie does not have a backdrop URL, log this and return None
        logging.info("Movie skipped due to missing backdrop image for user_id: {}".format(user_id))
        return None

    # async def fetch_and_render_movie(self, template_name='movie.html'):
    #     # logging.info("Fetching and rendering movie")
    #     if not self.current_displayed_movie:
    #         logging.info("No current movie to display")
    #         return None
    #
    #     # Check if the current movie has a backdrop URL, and if so, render it
    #     if 'backdrop_url' in self.current_displayed_movie and self.current_displayed_movie['backdrop_url']:
    #         return await render_template(template_name,
    #                                      movie=self.current_displayed_movie,
    #                                      previous_count=len(self.previous_movies_stack))
    #
    #     # If the movie does not have a backdrop URL, log this and return None
    #     logging.info("Movie skipped due to missing backdrop image")
    #     return None

    # async def next_movie(self):
    #     # logging.info("Fetching next movie")
    #     if self.current_displayed_movie:
    #         self.previous_movies_stack.append(self.current_displayed_movie)
    #     if self.future_movies_stack:
    #         self.current_displayed_movie = self.future_movies_stack.pop()
    #     elif not self.movie_queue.empty():
    #         logging.info("Pulling movie from movie queue")  # Added logging
    #         self.current_displayed_movie = await self.movie_queue.get()
    #     else:
    #         self.current_displayed_movie = None
    #
    #     return await self.fetch_and_render_movie()

    async def next_movie(self, user_id):
        user_queue = await self.get_user_queue(user_id)
        if user_queue.empty():
            logging.info(f"No more movies in queue for user_id: {user_id}")
            return None

        if self.future_movies_stack[user_id]:
            self.current_displayed_movie[user_id] = self.future_movies_stack[user_id].pop()
        else:
            self.current_displayed_movie[user_id] = await user_queue.get()

        return await self.fetch_and_render_movie(user_id)

    # async def previous_movie(self):
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
        if self.previous_movies_stack[user_id]:
            self.future_movies_stack[user_id].append(self.current_displayed_movie[user_id])
            self.current_displayed_movie[user_id] = self.previous_movies_stack[user_id].pop()
        else:
            logging.info(f"No previous movies for user_id: {user_id}")
            return None

        return await self.fetch_and_render_movie(user_id)

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

        # Assuming movie_queue_manager can handle user-specific tasks
        await self.movie_queue_manager.stop_populate_task(user_id)
        await self.movie_queue_manager.empty_queue(user_id)

        self.current_displayed_movie[user_id] = None
        logging.info(f"Filters set for user_id: {user_id} in {asyncio.get_event_loop().time() - start_time} seconds")
        return await render_template('set_filters.html')

    # async def home(self):
    #     logging.info("Accessing home")
    #     return await render_template('home.html', default_backdrop_url=self.default_backdrop_url)

    async def home(self, user_id):
        logging.info(f"Accessing home for user_id: {user_id}")
        # Include any user-specific data if needed. Otherwise, keep it as is.
        return await render_template('home.html', default_backdrop_url=self.default_backdrop_url)

    async def filtered_movie(self, user_id, form_data):
        logging.info(f"Filtering movie for user_id: {user_id}")
        new_criteria = extract_movie_filter_criteria(form_data)

        # Assuming the movie_queue_manager can handle user-specific tasks
        await self.movie_queue_manager.stop_populate_task(user_id)
        await self.movie_queue_manager.empty_queue(user_id)
        await self.movie_queue_manager.set_criteria(user_id, new_criteria)

        # Start populating the movie queue for this user with the new criteria
        self.movie_queue_manager.populate_task[user_id] = asyncio.create_task(
            self.movie_queue_manager.populate(user_id)
        )

        logging.info(f"Criteria updated for user_id: {user_id}, repopulating movie queue")
        await asyncio.sleep(20)  # Giving time for queue to populate
        return await self.fetch_and_render_movie(user_id)

    # async def filtered_movie(self, form_data):
    #     logging.info("Filtering movie")
    #     new_criteria = extract_movie_filter_criteria(form_data)
    #     self.criteria = new_criteria
    #     await self.movie_queue_manager.stop_populate_task()
    #     await self.movie_queue_manager.empty_queue()
    #     await self.movie_queue_manager.set_criteria(self.criteria)
    #     self.movie_queue_manager.populate_task = asyncio.create_task(self.movie_queue_manager.populate())
    #     logging.info("Criteria updated, repopulating movie queue")
    #     await asyncio.sleep(20)  # Giving time for queue to populate
    #     return await self.fetch_and_render_movie()


# Simulated user IDs for testing
user_ids = ["user1", "user2", "user3"]


async def main():
    dbconfig = Config.STACKHERO_DB_CONFIG
    user_ids = ["user1", "user2", "user3"]
    movie_managers = {user_id: MovieManager(dbconfig, user_id) for user_id in user_ids}

    # Starting the MovieManager for each user
    for user_id, manager in movie_managers.items():
        await manager.start_for_user(user_id)
        print(f"Started MovieManager for user: {user_id}")

    # Simulate interactions for each user
    for user_id, manager in movie_managers.items():
        await asyncio.sleep(2)  # Wait for queue to populate
        # You can add specific user interactions here, like fetching movies
        # Example: rendered_movie = await manager.fetch_and_render_movie()
        # next_movie_render = await manager.next_movie()
        # prev_movie_render = await manager.previous_movie()


if __name__ == "__main__":
    asyncio.run(main())

# # Main function for testing...
# async def main():
#     dbconfig = Config.STACKHERO_DB_CONFIG
#     movie_manager = MovieManager(dbconfig)
#     await movie_manager.start()
#     await asyncio.sleep(10)  # Wait for queue to populate
#     # rendered_movie = await movie_manager.fetch_and_render_movie()
#     # next_movie_render = await movie_manager.next_movie()
#     # prev_movie_render = await movie_manager.previous_movie()


# if __name__ == "__main__":
#     asyncio.run(main())
