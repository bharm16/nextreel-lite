import asyncio
import logging
import os
import time
import traceback

import httpx
from quart import current_app

from scripts.movie import Movie
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, db_pool

# Configure logging for better clarity
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(filename)s - %(funcName)s - %(levelname)s - %(message)s",
)
# Set the working directory to the parent directory for relative path resolutions
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)
logging.debug(f"Current working directory after change: {os.getcwd()}")


class MovieQueue:
    _instance = None

    def __new__(cls, *args, **kwargs):
        # Ensuring Singleton pattern
        if not isinstance(cls._instance, cls):
            cls._instance = super(MovieQueue, cls).__new__(cls)
            logging.info("Creating a new instance of MovieQueue")
        return cls._instance

    def __init__(self, db_config, queue, criteria=None):
        # Avoid reinitialization if already initialized
        if not hasattr(self, "_initialized"):
            logging.info("Initializing MovieQueue")
            self.db_config = db_config
            self.queue = queue
            self.movie_fetcher = ImdbRandomMovieFetcher(db_pool)
            self.criteria = criteria or {}
            self.lock = asyncio.Lock()
            logging.info(f"MovieQueue instance created with criteria: {self.criteria}")
            self.populate_task = None  # Async task for populating the queue
            self._initialized = True
            self.movie_enqueue_count = 0  # Add a counter for movies enqueued
            self.user_queues = {}  # Dictionary to store user-specific queues
            self.stop_flags = (
                {}
            )  # Tracks whether to stop the populate task for each user
            self.space_available = asyncio.Event()
            self.space_available.set()  # Initially, assume there's space available.

    async def set_stop_flag(self, user_id, stop=True):
        """Sets the stop flag for a given user's populate task."""
        logging.info(f"Setting stop flag for user_id: {user_id} to {stop}")
        self.stop_flags[user_id] = stop

    async def check_stop_flag(self, user_id):
        """Checks if the stop flag is set for a given user's populate task."""
        stop_flag = self.stop_flags.get(user_id, False)
        logging.info(f"Stop flag for user_id {user_id}: {stop_flag}")
        return stop_flag

    async def get_user_queue(self, user_id):
        try:
            if user_id not in self.user_queues:
                logging.info(f"Creating new queue for user_id: {user_id}")
                self.user_queues[user_id] = {
                    "queue": asyncio.Queue(maxsize=20),
                    "criteria": {},
                }
            return self.user_queues[user_id]["queue"]
        except Exception as e:
            logging.error(
                f"Unexpected error in get_user_queue for user_id {user_id}: {e}",
                exc_info=True,
            )
            raise

    async def add_user(self, user_id, criteria):
        try:
            logging.info(f"Adding new user with user_id: {user_id} and criteria: {criteria}")
            if user_id not in self.user_queues:
                self.user_queues[user_id] = {
                    "queue": asyncio.Queue(maxsize=20),
                    "criteria": criteria,
                }
                self.user_queues[user_id]["populate_task"] = asyncio.create_task(
                    self.populate(user_id)
                )
                logging.info(f"Population task started for user_id: {user_id}")
        except Exception as e:
            logging.error(f"Failed to add user_id {user_id}: {e}", exc_info=True)

    async def set_criteria(self, user_id, new_criteria):
        try:
            logging.info(f"Updating criteria for user_id {user_id} to {new_criteria}")
            if user_id not in self.user_queues:
                await self.get_user_queue(user_id)

            async with self.lock:
                self.user_queues[user_id]["criteria"] = new_criteria
        except Exception as e:
            logging.error(f"Failed to set criteria for user_id {user_id}: {e}", exc_info=True)

    async def start_populate_task(self, user_id):
        try:
            logging.info(f"Attempting to start populate task for user_id {user_id}")
            user_queue_info = self.user_queues.get(user_id)
            if user_queue_info and (
                not user_queue_info.get("populate_task")
                or user_queue_info["populate_task"].done()
            ):
                user_queue_info["populate_task"] = asyncio.create_task(
                    self.populate(user_id)
                )
                logging.info(f"Populate task started for user_id: {user_id}")
            else:
                logging.info(f"Populate task already running or not ready to start for user_id: {user_id}")
        except Exception as e:
            logging.error(f"Failed to start populate task for user_id {user_id}: {e}", exc_info=True)

    async def stop_populate_task(self, user_id):
        try:
            logging.info(f"Stopping populate task for user_id {user_id}")
            await self.set_stop_flag(user_id, True)

            user_queue_info = self.user_queues.get(user_id)
            if user_queue_info and user_queue_info.get("populate_task"):
                user_queue_info["populate_task"].cancel()
                await user_queue_info["populate_task"]
                logging.info(f"Populate task stopped for user_id {user_id}")
        except asyncio.CancelledError:
            logging.info(f"Populate task cancellation confirmed for user_id {user_id}")
        except Exception as e:
            logging.error(f"Error stopping populate task for user_id {user_id}: {e}", exc_info=True)

    async def populate(self, user_id, completion_event=None):
        max_queue_size = 15
        logging.info(f"Starting population for user_id {user_id}")
        while True:
            try:
                user_queue = await self.get_user_queue(user_id)
                current_queue_size = user_queue.qsize()

                if current_queue_size >= max_queue_size:
                    logging.info(f"Queue full for user_id {user_id}. Waiting for space...")
                    self.space_available.clear()
                    await self.space_available.wait()

                if current_queue_size <= 1:
                    if await self.check_stop_flag(user_id):
                        logging.info(f"Stop flag set for user_id {user_id}. Ending population.")
                        break

                    logging.info(f"Queue below threshold for user_id {user_id}. Loading more movies...")
                    await self.load_movies_into_queue(user_id)
            except asyncio.CancelledError:
                logging.info(f"Populate task cancelled for user_id {user_id}")
                break
            except Exception as e:
                logging.error(f"Exception during populate for user_id {user_id}: {e}", exc_info=True)
            finally:
                if completion_event:
                    completion_event.set()
                logging.info(f"Population iteration completed for user_id {user_id}")

    async def load_movies_into_queue(self, user_id):
        logging.info(f"Loading movies for user_id {user_id}")
        try:
            user_criteria = self.user_queues[user_id]["criteria"]
            rows = await self.movie_fetcher.fetch_random_movies15(user_criteria)
            if rows:
                logging.info(f"Fetched {len(rows)} movies for user_id {user_id}")
                tasks = [
                    self.fetch_and_enqueue_movie(row["tconst"], user_id)
                    for row in rows
                ]
                await asyncio.gather(*tasks)
            else:
                logging.warning(f"No movies found for user_id {user_id}")
        except Exception as e:
            logging.error(f"Error loading movies for user_id {user_id}: {e}", exc_info=True)

    async def fetch_and_enqueue_movie(self, tconst, user_id):
        try:
            logging.info(f"Fetching movie with tconst {tconst} for user_id {user_id}")
            movie = Movie(tconst, self.db_config)
            movie_data = await movie.get_movie_data()
            if movie_data:
                user_queue = await self.get_user_queue(user_id)
                if not user_queue.full():
                    await user_queue.put(movie_data)
                    logging.info(f"Enqueued movie {movie_data['title']} for user_id {user_id}")
        except Exception as e:
            logging.error(f"Error fetching movie {tconst} for user_id {user_id}: {e}", exc_info=True)

    def is_task_running(self):
        """Check if the populate task is running."""
        if self.populate_task is None:
            logging.info("Populate task has not been initialized.")
            return False

        if self.populate_task.done():
            try:
                result = self.populate_task.result()
                logging.info(f"Populate task completed successfully with result: {result}")
            except asyncio.CancelledError:
                logging.info("Populate task was cancelled.")
            except Exception as e:
                logging.error(f"Populate task raised an exception: {e}", exc_info=True)
            return False
        else:
            logging.info("Populate task is currently running.")
            return True

    # async def main():
    #     # Initialize the MovieQueue
    #     movie_queue_manager = MovieQueue(Config.STACKHERO_DB_CONFIG, asyncio.Queue())
    #
    #     # User-specific criteria
    #     user_criteria = {
    #         "user1": {"min_year": 1990, "max_year": 2023, "min_rating": 7.0, "max_rating": 10, "title_type": "movie", "language": "en", "genres": ["Action"]},
    #         "user2": {"min_year": 1980, "max_year": 2023, "min_rating": 6.0, "max_rating": 10, "title_type": "movie", "language": "en", "genres": ["Comedy"]}
    #     }
    #
    #     # Set criteria and start population tasks for each user
    #     for user_id, criteria in user_criteria.items():
    #         logging.info(f"Setting criteria for {user_id}: {criteria}")
    #         await movie_queue_manager.set_criteria(user_id, criteria)
    #         movie_queue_manager.start_populate_task(user_id)
    #
    #     # Simulate a period of operation
    #     # await asyncio.sleep(60)  # Simulate the queue population for 60 seconds
    #
    #     # Stop population tasks and empty queues for each user
    #     for user_id in user_criteria.keys():
    #         await movie_queue_manager.stop_populate_task(user_id)
    #         await movie_queue_manager.empty_queue(user_id)
    #         logging.info(f"Queue for {user_id} stopped and emptied")
    #
    #     logging.info("All tasks completed")
    #
    # if __name__ == "__main__":
    #     asyncio.run(main())
