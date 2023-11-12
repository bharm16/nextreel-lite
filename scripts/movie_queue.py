import asyncio
import logging
import os
from asyncio import Queue

import httpx

from config import Config
from scripts.movie import get_tmdb_id_by_tconst, Movie
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher
from scripts.tmdb_data import get_movie_info_by_tmdb_id

# Configure logging
logging.basicConfig(level=logging.INFO)

# Set the working directory to the parent directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)
logging.debug(f"Current working directory after change: {os.getcwd()}")


class MovieQueue:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not isinstance(cls._instance, cls):
            cls._instance = super(MovieQueue, cls).__new__(cls)
            logging.info("Creating a new instance of MovieQueue")
        return cls._instance

    def __init__(self, db_config, queue, criteria=None):
        if not hasattr(self, '_initialized'):
            self.db_config = db_config
            self.queue = queue
            self.movie_fetcher = ImdbRandomMovieFetcher(self.db_config)
            self.criteria = criteria or {}
            self.lock = asyncio.Lock()
            logging.info(f"MovieQueue instance created with criteria: {self.criteria}")
            self.populate_task = None
            self._initialized = True

    async def set_criteria(self, new_criteria):
        async with self.lock:
            self.criteria = new_criteria
            logging.info(f"MovieQueue criteria updated to: {self.criteria}")

    async def stop_populate_task(self):
        if self.populate_task:
            self.populate_task.cancel()
            try:
                await self.populate_task
            except asyncio.CancelledError:
                logging.info("Populate task cancelled")
            logging.info("Populate task stopped")

    async def empty_queue(self):
        async with self.lock:
            while not self.queue.empty():
                await self.queue.get()
            logging.info("Movie queue emptied")

    async def populate(self):
        max_queue_size = 15
        while True:
            try:
                if self.queue.qsize() >= max_queue_size:
                    logging.info(f"Queue has reached maximum size of {max_queue_size}, stopping populate task.")
                    break

                current_queue_size = self.queue.qsize()
                if current_queue_size < 5:
                    logging.info("Queue size below threshold, loading more movies...")
                    await self.load_movies_into_queue()
                else:
                    logging.info(f"Queue size sufficient: {current_queue_size}")

                await asyncio.sleep(1)
            except asyncio.CancelledError:
                logging.info("Populate task has been cancelled")
                break
            except Exception as e:
                logging.exception(f"Exception occurred in populate: {e}")
                await asyncio.sleep(5)

    def is_task_running(self):
        running = self.populate_task and not self.populate_task.done()
        logging.info(f"Populate task running: {running}")
        return running

    async def fetch_and_enqueue_movie(self, tconst, client):
        async with self.lock:
            if self.queue.qsize() >= 10:
                logging.info("Queue is full. Current movies in the queue:")
                queue_snapshot = list(self.queue._queue)
                for movie in queue_snapshot:
                    logging.info(f"Movie Title: {movie.get('title', 'N/A')}, tconst: {movie.get('tconst', 'N/A')}")
                return

        movie = Movie(tconst, self.db_config, client)
        movie_data_imdb = await movie.get_movie_data()
        tmdb_id = await get_tmdb_id_by_tconst(tconst, client)
        movie_data_tmdb = await get_movie_info_by_tmdb_id(tmdb_id, client)
        movie_data_imdb['backdrop_path'] = movie_data_tmdb.get('backdrop_path', None)

        async with self.lock:
            await self.queue.put(movie_data_imdb)
            logging.info(f"Enqueued movie '{movie_data_imdb.get('title', 'N/A')}' with tconst: {tconst}")

    async def load_movies_into_queue(self):
        async with httpx.AsyncClient() as client:
            rows = await self.movie_fetcher.fetch_random_movies25(self.criteria, client=client)
            tasks = [self.fetch_and_enqueue_movie(row['tconst'], client) for row in rows if row]
            await asyncio.gather(*tasks)

    async def update_criteria_and_reset(self, new_criteria):
        await self.set_criteria(new_criteria)
        await self.empty_queue()
        self.populate_task = asyncio.create_task(self.populate())
        logging.info("Populate task restarted")


async def main():
    movie_queue = Queue()
    movie_queue_manager = MovieQueue(Config.STACKHERO_DB_CONFIG, movie_queue)

    criteria = {
        "min_year": 1900,
        "max_year": 2023,
        "min_rating": 7.0,
        "max_rating": 10,
        "title_type": "movie",
        "language": "en",
        "genres": ["Action", "Drama"]
    }
    await movie_queue_manager.set_criteria(criteria)

    movie_queue_manager.populate_task = asyncio.create_task(movie_queue_manager.populate())

    await asyncio.sleep(5)  # Allow time for the queue to populate

    logging.info("Movies loaded into the queue:")
    while not movie_queue.empty():
        movie = await movie_queue.get()
        logging.info(f"Movie Title: {movie.get('title', 'N/A')}, tconst: {movie.get('tconst', 'N/A')}")

    await movie_queue_manager.stop_populate_task()
    await movie_queue_manager.empty_queue()

    logging.info(f"Is the MovieQueue task still running? {movie_queue_manager.is_task_running()}")


if __name__ == "__main__":
    asyncio.run(main())
