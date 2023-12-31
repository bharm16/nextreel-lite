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

    async def set_criteria(self, new_criteria):
        async with self.lock:
            self.criteria = new_criteria
            logging.info(f"MovieQueue criteria updated to: {self.criteria}")

    async def stop_populate_task(self):
        # Stop the population task if it's running
        if self.populate_task:
            self.populate_task.cancel()
            try:
                await self.populate_task
            except asyncio.CancelledError:
                logging.info("Populate task cancelled")
            logging.info("Populate task stopped")

    async def empty_queue(self):
        # Empty the current movie queue
        async with self.lock:
            while not self.queue.empty():
                await self.queue.get()
            logging.info("Movie queue emptied")

    async def populate(self):
        max_queue_size = 15
        max_queue_size_with_buffer = 20
        while True:
            try:
                if self.queue.qsize() == max_queue_size:
                    logging.info(f"Queue has reached maximum size of {max_queue_size}, stopping populate task.")
                    # break

                current_queue_size = self.queue.qsize()
                if current_queue_size <= 1:
                    logging.info("Queue size below threshold, loading more movies...")
                    await self.load_movies_into_queue()
                # else:
                #     logging.info(f"Queue size sufficient: {current_queue_size}")

                await asyncio.sleep(1)  # Non-blocking sleep to prevent hogging the CPU
            except asyncio.CancelledError:
                logging.info("Populate task has been cancelled")
                break
            except Exception as e:
                logging.exception(f"Exception occurred in populate: {e}")
                await asyncio.sleep(5)

    def is_task_running(self):
        # Check if the population task is still running
        running = self.populate_task and not self.populate_task.done()
        logging.info(f"Populate task running: {running}")
        return running

    async def fetch_and_enqueue_movie(self, tconst):

        # Fetch and enqueue a single movie
        async with self.lock:
            if self.queue.qsize() == 15:
                logging.info("Queue is full. Current movies in the queue:")
                queue_snapshot = list(self.queue._queue)
                for movie in queue_snapshot:
                    logging.info(f"Movie Title: {movie.get('title')}, tconst: {movie.get('tconst')}")
                return

        movie = Movie(tconst, self.db_config)
        movie_data_tmdb = await movie.get_movie_data()  # Fetches data using TMDB ID via the Movie class

        if movie_data_tmdb:
            async with self.lock:
                await self.queue.put(movie_data_tmdb)
                self.movie_enqueue_count += 1  # Increment the counter

                logging.info(f" [{self.movie_enqueue_count}] Enqueued movie '{movie_data_tmdb.get('title')}' with "
                             f"tconst: {tconst}")
        else:
            logging.warning(f"No movie data found for tconst: {tconst}")

    async def load_movies_into_queue(self):
        # Ensures that the httpx client and tasks are created within Quart's event loop
        async with current_app.app_context():
            async with httpx.AsyncClient() as client:
                rows = await self.movie_fetcher.fetch_random_movies25(self.criteria, client=client)
                number_of_rows = len(rows)
                logging.info(f"Number of movies fetched: {number_of_rows}")

                tasks = []
                for row in rows:
                    if row:
                        task = asyncio.create_task(self.fetch_and_enqueue_movie(row['tconst']))
                        tasks.append(task)

                await asyncio.gather(*tasks)

    async def update_criteria_and_reset(self, new_criteria):
        # Update the criteria and reset the queue
        await self.set_criteria(new_criteria)
        await self.empty_queue()
        self.populate_task = asyncio.create_task(self.populate())
        logging.info("Populate task restarted")


# async def main():
#     # Main function for testing...
#     movie_queue = Queue()
#     movie_queue_manager = MovieQueue(Config.STACKHERO_DB_CONFIG, movie_queue)
#
#     criteria = {
#         "min_year": 1900,
#         "max_year": 2023,
#         "min_rating": 7.0,
#         "max_rating": 10,
#         "title_type": "movie",
#         "language": "en",
#         "genres": ["Action", "Drama"]
#     }
#     await movie_queue_manager.set_criteria(criteria)
#
#     movie_queue_manager.populate_task = asyncio.create_task(movie_queue_manager.populate())
#
#     await asyncio.sleep(5)  # Allow time for the queue to populate
#
#     await movie_queue_manager.stop_populate_task()
#     await movie_queue_manager.empty_queue()
#
#     logging.info(f"Is the MovieQueue task still running? {movie_queue_manager.is_task_running()}")
#
#
# if __name__ == "__main__":
#     asyncio.run(main())
