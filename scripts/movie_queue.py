import asyncio
import logging
import os
from asyncio import Queue
import httpx
from quart import current_app

from config import Config
from scripts.movie import Movie, TMDB_API_KEY
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher

# Configure logging for better clarity
logging.basicConfig(level=logging.INFO)

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
        if not hasattr(self, '_initialized'):
            self.db_config = db_config
            self.queue = queue
            self.movie_fetcher = ImdbRandomMovieFetcher(self.db_config)
            self.criteria = criteria or {}
            self.lock = asyncio.Lock()
            logging.info(f"MovieQueue instance created with criteria: {self.criteria}")
            self.populate_task = None  # Async task for populating the queue
            self._initialized = True
            self.movie_enqueue_count = 0  # Add a counter for movies enqueued
            self.user_queues = {}  # Dictionary to store user-specific queues

    async def get_user_queue(self, user_id):
        # Initialize user's queue and criteria if not exists
        if user_id not in self.user_queues:
            self.user_queues[user_id] = {'queue': asyncio.Queue(maxsize=20), 'criteria': {}}
        return self.user_queues[user_id]['queue']

    async def add_user(self, user_id, criteria):
        """
        Add a new user with specific criteria and start the population task for them.
        """
        if user_id not in self.user_queues:
            self.user_queues[user_id] = {'queue': asyncio.Queue(maxsize=20), 'criteria': criteria}
            self.user_queues[user_id]['populate_task'] = asyncio.create_task(self.populate(user_id))
            logging.info(f"Added and started population task for new user: {user_id}")

    async def set_criteria(self, user_id, new_criteria):
        # Ensure the user's queue and criteria are initialized
        if user_id not in self.user_queues:
            await self.get_user_queue(user_id)

        # Set the new criteria
        async with self.lock:
            self.user_queues[user_id]['criteria'] = new_criteria
            logging.info(f"Criteria for user_id {user_id} updated to: {new_criteria}")
            # Optionally, trigger repopulation based on new criteria
            await self.empty_queue(user_id)
            await self.populate(user_id)

    async def stop_populate_task(self, user_id):
        user_queue_info = self.user_queues.get(user_id)
        if user_queue_info and user_queue_info.get('populate_task'):
            user_queue_info['populate_task'].cancel()
            try:
                await user_queue_info['populate_task']
                logging.info(f"Populate task for user_id {user_id} cancelled")
            except asyncio.CancelledError:
                logging.info(f"Populate task for user_id {user_id} stopped")

    async def empty_queue(self, user_id):
        user_queue = self.user_queues.get(user_id)
        if user_queue:
            async with self.lock:
                while not user_queue.empty():
                    await user_queue.get()
                logging.info(f"Movie queue for user_id {user_id} emptied")

    async def populate(self, user_id):
        max_queue_size = 15
        while True:
            try:
                user_queue = await self.get_user_queue(user_id)
                current_queue_size = user_queue.qsize()

                # Check if the queue has reached its maximum size
                if current_queue_size >= max_queue_size:
                    logging.info(f"User queue for user_id: {user_id} has reached maximum size of {max_queue_size}.")
                    # Sleep for a longer duration before checking again
                    await asyncio.sleep(10)
                    continue

                # Load more movies if the queue size is below a threshold
                if current_queue_size <= 1:
                    logging.info(f"Queue size below threshold for user_id: {user_id}, loading more movies...")
                    await self.load_movies_into_queue(user_id)

                # Non-blocking sleep to prevent CPU hogging
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                logging.info(f"Populate task for user_id: {user_id} has been cancelled")
                break
            except Exception as e:
                logging.exception(f"Exception in populate for user_id: {user_id}: {e}")
                # Sleep before retrying
                await asyncio.sleep(5)

    def is_task_running(self):
        # Check if the population task is still running
        running = self.populate_task and not self.populate_task.done()
        logging.info(f"Populate task running: {running}")
        return running

    async def fetch_and_enqueue_movie(self, tconst, user_id):
        movie = Movie(tconst, self.db_config)
        movie_data_tmdb = await movie.get_movie_data()

        if movie_data_tmdb:
            user_queue = await self.get_user_queue(user_id)
            async with self.lock:
                if not user_queue.full():
                    await user_queue.put(movie_data_tmdb)
                    self.movie_enqueue_count += 1
                    logging.info(
                        f"[{self.movie_enqueue_count}] Enqueued movie '{movie_data_tmdb.get('title')}' with tconst: {tconst} for user_id: {user_id}")

    async def load_movies_into_queue(self, user_id):
        async with current_app.app_context(), httpx.AsyncClient() as client:
            rows = await self.movie_fetcher.fetch_random_movies25(self.criteria, client)
            tasks = [asyncio.create_task(self.fetch_and_enqueue_movie(row['tconst'], user_id)) for row in rows if row]
            await asyncio.gather(*tasks)

    async def update_criteria_and_reset(self, user_id, new_criteria):
        # Update the criteria and reset the queue for a specific user
        await self.set_criteria(user_id, new_criteria)
        await self.empty_queue(user_id)

        # Restart the populate task for the user
        user_queue_info = self.user_queues.get(user_id)
        if user_queue_info:
            user_queue_info['populate_task'] = asyncio.create_task(self.populate(user_id))
            logging.info(f"Populate task restarted for user_id: {user_id}")


async def main():
    # Initialize the MovieQueue
    movie_queue_manager = MovieQueue(Config.STACKHERO_DB_CONFIG, asyncio.Queue())

    # User-specific criteria
    user_criteria = {
        "user1": {"min_year": 1990, "max_year": 2023, "min_rating": 7.0, "max_rating": 10, "title_type": "movie", "language": "en", "genres": ["Action"]},
        "user2": {"min_year": 1980, "max_year": 2023, "min_rating": 6.0, "max_rating": 10, "title_type": "movie", "language": "en", "genres": ["Comedy"]}
    }

    # Create and start population tasks for each user
    for user_id, criteria in user_criteria.items():
        logging.info(f"Setting criteria and starting populate task for {user_id}: {criteria}")
        await movie_queue_manager.set_criteria(user_id, criteria)
        asyncio.create_task(movie_queue_manager.populate(user_id))



    # Simulate a period of operation
    # await asyncio.sleep(60)  # Simulate the queue population for 60 seconds

    # Stop population tasks and empty queues for each user
    for user_id in user_criteria.keys():
        await movie_queue_manager.stop_populate_task(user_id)
        await movie_queue_manager.empty_queue(user_id)
        logging.info(f"Queue for {user_id} stopped and emptied")

    logging.info("All tasks completed")

if __name__ == "__main__":
    asyncio.run(main())
