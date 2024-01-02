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
            self.locks = {}  # Initialize locks dictionary
            self.queues = {}  # Initialize queues dictionary
            self.movie_fetchers = {}  # Initialize movie_fetchers dictionary
            self.populate_tasks = {}  # Initialize populate_tasks dictionary
            self.movie_enqueue_count = {}  # Initialize movie_enqueue_count dictionary
            logging.info(f"MovieQueue instance created with criteria: {self.criteria}")
            self.populate_task = None
            self._initialized = True
            self.movie_enqueue_count = {}  # Initialize movie_enqueue_count dictionary
            self.populate_tasks = {}  # Initialize populate_tasks dictionary
            # self.movie_enqueue_count[user_id] = 0

    # def __init__(self, db_config, queue, criteria=None):
    #     # Avoid reinitialization if already initialized
    #     if not hasattr(self, '_initialized'):
    #         self.db_config = db_config
    #         self.queue = queue
    #         self.movie_fetcher = ImdbRandomMovieFetcher(self.db_config)
    #         self.criteria = criteria or {}
    #         self.lock = asyncio.Lock()
    #         logging.info(f"MovieQueue instance created with criteria: {self.criteria}")
    #         self.populate_task = None  # Async task for populating the queue
    #         self._initialized = True
    #         self.movie_enqueue_count = 0  # Add a counter for movies enqueued

    # async def set_criteria(self, new_criteria):
    #     async with self.lock:
    #         self.criteria = new_criteria
    #         logging.info(f"MovieQueue criteria updated to: {self.criteria}")

    async def set_criteria(self, user_id, new_criteria):
        if user_id not in self.locks:
            self.locks[user_id] = asyncio.Lock()

        async with self.locks[user_id]:
            self.criteria[user_id] = new_criteria
            # Initialize movie enqueue count for the user if not already initialized
            if user_id not in self.movie_enqueue_count:
                self.movie_enqueue_count[user_id] = 0
            logging.info(f"MovieQueue criteria updated for user {user_id} to: {new_criteria}")

    # async def stop_populate_task(self):
    #     # Stop the population task if it's running
    #     if self.populate_task:
    #         self.populate_task.cancel()
    #         try:
    #             await self.populate_task
    #         except asyncio.CancelledError:
    #             logging.info("Populate task cancelled")
    #         logging.info("Populate task stopped")

    async def stop_populate_task(self, user_id):
        if user_id in self.populate_tasks and self.populate_tasks[user_id]:
            self.populate_tasks[user_id].cancel()
            try:
                await self.populate_tasks[user_id]
            except asyncio.CancelledError:
                logging.info(f"Populate task for user {user_id} cancelled")
            logging.info(f"Populate task for user {user_id} stopped")

    # async def empty_queue(self):
    #     # Empty the current movie queue
    #     async with self.lock:
    #         while not self.queue.empty():
    #             await self.queue.get()
    #         logging.info("Movie queue emptied")

    async def empty_queue(self, user_id):
        if user_id not in self.queues:
            self.queues[user_id] = Queue()

        async with self.locks[user_id]:
            while not self.queues[user_id].empty():
                await self.queues[user_id].get()
            logging.info(f"Movie queue for user {user_id} emptied")

    # async def populate(self):
    #     max_queue_size = 15
    #     max_queue_size_with_buffer = 20
    #     while True:
    #         try:
    #             if self.queue.qsize() == max_queue_size:
    #                 logging.info(f"Queue has reached maximum size of {max_queue_size}, stopping populate task.")
    #                 # break
    #
    #             current_queue_size = self.queue.qsize()
    #             if current_queue_size <= 1:
    #                 logging.info("Queue size below threshold, loading more movies...")
    #                 await self.load_movies_into_queue()
    #             # else:
    #             #     logging.info(f"Queue size sufficient: {current_queue_size}")
    #
    #             await asyncio.sleep(1)  # Non-blocking sleep to prevent hogging the CPU
    #         except asyncio.CancelledError:
    #             logging.info("Populate task has been cancelled")
    #             break
    #         except Exception as e:
    #             logging.exception(f"Exception occurred in populate: {e}")
    #             await asyncio.sleep(5)

    async def populate(self, user_id):
        max_queue_size = 15
        max_queue_size_with_buffer = 20

        if user_id not in self.queues:
            self.queues[user_id] = Queue()
        if user_id not in self.movie_fetchers:
            self.movie_fetchers[user_id] = ImdbRandomMovieFetcher(self.db_config)

        while True:
            try:
                if self.queues[user_id].qsize() == max_queue_size:
                    logging.info(
                        f"Queue for user {user_id} has reached maximum size of {max_queue_size}, stopping populate task.")
                    # break  # Uncomment if you want to stop the task after reaching max size

                current_queue_size = self.queues[user_id].qsize()
                if current_queue_size <= 1:
                    logging.info(f"Queue size for user {user_id} below threshold, loading more movies...")
                    await self.load_movies_into_queue(user_id)
                # else:
                #     logging.info(f"Queue size for user {user_id} sufficient: {current_queue_size}")

                await asyncio.sleep(1)  # Non-blocking sleep to prevent hogging the CPU
            except asyncio.CancelledError:
                logging.info(f"Populate task for user {user_id} has been cancelled")
                break
            except Exception as e:
                logging.exception(f"Exception occurred in populate for user {user_id}: {e}")
                await asyncio.sleep(5)

    # The load_movies_into_queue method also needs to be updated to handle user-specific operations.

    # def is_task_running(self):
    #     # Check if the population task is still running
    #     running = self.populate_task and not self.populate_task.done()
    #     logging.info(f"Populate task running: {running}")
    #     return running

    def is_task_running(self, user_id):
        # Check if the population task for a specific user is still running
        if user_id in self.populate_tasks:
            running = self.populate_tasks[user_id] and not self.populate_tasks[user_id].done()
            logging.info(f"Populate task for user {user_id} running: {running}")
            return running
        else:
            logging.info(f"No populate task found for user {user_id}")
            return False

    # async def fetch_and_enqueue_movie(self, tconst):
    #
    #     # Fetch and enqueue a single movie
    #     async with self.lock:
    #         if self.queue.qsize() == 15:
    #             logging.info("Queue is full. Current movies in the queue:")
    #             queue_snapshot = list(self.queue._queue)
    #             for movie in queue_snapshot:
    #                 logging.info(f"Movie Title: {movie.get('title')}, tconst: {movie.get('tconst')}")
    #             return
    #
    #     movie = Movie(tconst, self.db_config)
    #     movie_data_tmdb = await movie.get_movie_data()  # Fetches data using TMDB ID via the Movie class
    #
    #     if movie_data_tmdb:
    #         async with self.lock:
    #             await self.queue.put(movie_data_tmdb)
    #             self.movie_enqueue_count += 1  # Increment the counter
    #
    #             logging.info(f" [{self.movie_enqueue_count}] Enqueued movie '{movie_data_tmdb.get('title')}' with "
    #                          f"tconst: {tconst}")
    #     else:
    #         logging.warning(f"No movie data found for tconst: {tconst}")

    async def fetch_and_enqueue_movie(self, tconst, user_id):
        # Fetch and enqueue a single movie for a specific user
        if user_id not in self.queues:
            logging.warning(f"No queue found for user {user_id}")
            return

        user_queue = self.queues[user_id]

        async with self.lock:
            if user_queue.qsize() == 15:
                logging.info(f"Queue for user {user_id} is full. Current movies in the queue:")
                queue_snapshot = list(user_queue._queue)
                for movie in queue_snapshot:
                    logging.info(f"Movie Title: {movie.get('title')}, tconst: {movie.get('tconst')}")
                return

        movie = Movie(tconst, self.db_config)
        movie_data_tmdb = await movie.get_movie_data()  # Fetches data using TMDB ID via the Movie class

        if movie_data_tmdb:
            async with self.lock:
                await user_queue.put(movie_data_tmdb)
                # Safely increment the counter for the specific user
                if user_id in self.movie_enqueue_count:
                    self.movie_enqueue_count[user_id] += 1
                else:
                    self.movie_enqueue_count[user_id] = 1

                logging.info(
                    f" [{self.movie_enqueue_count[user_id]}] Enqueued movie '{movie_data_tmdb.get('title')}' for user {user_id} with tconst: {tconst}")
        else:
            logging.warning(f"No movie data found for tconst: {tconst}")

    # async def load_movies_into_queue(self):
    #     # Ensures that the httpx client and tasks are created within Quart's event loop
    #     async with current_app.app_context():
    #         async with httpx.AsyncClient() as client:
    #             rows = await self.movie_fetcher.fetch_random_movies25(self.criteria, client=client)
    #             number_of_rows = len(rows)
    #             logging.info(f"Number of movies fetched: {number_of_rows}")
    #
    #             tasks = []
    #             for row in rows:
    #                 if row:
    #                     task = asyncio.create_task(self.fetch_and_enqueue_movie(row['tconst']))
    #                     tasks.append(task)
    #
    #             await asyncio.gather(*tasks)

    async def load_movies_into_queue(self, user_id):
        # Use httpx.AsyncClient directly without relying on current_app.app_context
        async with httpx.AsyncClient() as client:
            # Fetch movies based on the criteria
            # Ensure that self.movie_fetcher is already initialized with the necessary configuration
            rows = await self.movie_fetchers[user_id].fetch_random_movies25(self.criteria[user_id], client=client)
            number_of_rows = len(rows)
            logging.info(f"Number of movies fetched: {number_of_rows}")

            # Create tasks to enqueue each movie
            tasks = []
            for row in rows:
                if row:
                    # Pass user_id to fetch_and_enqueue_movie
                    task = asyncio.create_task(self.fetch_and_enqueue_movie(row['tconst'], user_id))
                    tasks.append(task)

            # Gather all tasks to run them concurrently
            await asyncio.gather(*tasks)

    # async def update_criteria_and_reset(self, new_criteria):
    #     # Update the criteria and reset the queue
    #     await self.set_criteria(new_criteria)
    #     await self.empty_queue()
    #     self.populate_task = asyncio.create_task(self.populate())
    #     logging.info("Populate task restarted")

    async def update_criteria_and_reset(self, new_criteria, user_id):
        # Update the criteria and reset the queue for a specific user
        await self.set_criteria(new_criteria)
        await self.empty_queue(user_id)  # empty_queue method needs to be updated to accept user_id
        self.populate_task[user_id] = asyncio.create_task(self.populate(user_id))  # populate method also needs user_id
        logging.info("Populate task restarted for user_id: {}".format(user_id))


# Configure logging
logging.basicConfig(level=logging.INFO)


async def main():
    db_config = Config.STACKHERO_DB_CONFIG
    user_ids = ["user1", "user2", "user3"]  # Simulated user IDs for testing

    movie_queues = {user_id: MovieQueue(db_config, Queue()) for user_id in user_ids}

    criteria = {
        "min_year": 1900,
        "max_year": 2023,
        "min_rating": 7.0,
        "max_rating": 10,
        "title_type": "movie",
        "language": "en",
        "genres": ["Action", "Drama"]
    }

    for user_id in user_ids:
        await movie_queues[user_id].set_criteria(user_id, criteria)
        movie_queues[user_id].populate_task = asyncio.create_task(movie_queues[user_id].populate(user_id))
        logging.info(f"Started populate task for {user_id}")

    # Immediate check after starting populate tasks
    for user_id in user_ids:
        is_running = movie_queues[user_id].is_task_running(user_id)
        logging.info(f"Immediate check - Populate task for {user_id} running: {is_running}")

    await asyncio.sleep(10)  # Allow time for queues to start populating

    # Check status after some time
    for user_id in user_ids:
        is_running = movie_queues[user_id].is_task_running(user_id)
        logging.info(f"Delayed check - Populate task for {user_id} running: {is_running}")

    # Stop and empty queues
    for user_id in user_ids:
        await movie_queues[user_id].stop_populate_task(user_id)
        await movie_queues[user_id].empty_queue(user_id)

if __name__ == "__main__":
    asyncio.run(main())



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
