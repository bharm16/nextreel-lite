import asyncio
import logging
import os
import time
import traceback

import httpx
from quart import current_app

from scripts.movie import Movie
from scripts.filter_backend import ImdbRandomMovieFetcher
from .interfaces import MovieFetcher
from settings import DatabaseConnectionPool

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
    """Manage per-user movie queues."""

    def __init__(self, db_pool: DatabaseConnectionPool, movie_fetcher: MovieFetcher, queue_size: int = 15):
        self.db_pool = db_pool
        self.movie_fetcher = movie_fetcher
        self.queue_size = queue_size
        self.lock = asyncio.Lock()

        self.movie_enqueue_count = 0
        self.user_queues = {}
        self.stop_flags = {}

    async def set_stop_flag(self, user_id, stop=True):
        """Sets the stop flag for a given user's populate task."""
        self.stop_flags[user_id] = stop

    async def check_stop_flag(self, user_id):
        """Checks if the stop flag is set for a given user's populate task."""
        return self.stop_flags.get(user_id, False)

    async def get_user_queue(self, user_id):
        try:
            if user_id not in self.user_queues:
                self.user_queues[user_id] = {
                    "queue": asyncio.Queue(maxsize=self.queue_size),
                    "criteria": {},
                    "seen_tconsts": set(),
                }
            return self.user_queues[user_id]["queue"]
        except Exception as e:
            logging.error(
                f"Unexpected error in get_user_queue for user_id: {user_id}: {e}",
                exc_info=True,
            )
            raise  # It's often a good idea to re-raise the exception after logging to not silently swallow errors.

    async def add_user(self, user_id, criteria):
        try:
            if user_id not in self.user_queues:
                self.user_queues[user_id] = {
                    "queue": asyncio.Queue(maxsize=self.queue_size),
                    "criteria": criteria,
                    "seen_tconsts": set(),
                }
                self.user_queues[user_id]["populate_task"] = asyncio.create_task(
                    self.populate(user_id)
                )
                logging.info(
                    f"Added and started population task for new user: {user_id}"
                )
        except Exception as e:
            tb_str = traceback.format_exception(e)
            logging.error("".join(tb_str))
            logging.error(
                f"Failed to add new user or start population task for user_id: {user_id}. Exception: {e}"
            )

    async def set_criteria(self, user_id, new_criteria):
        try:
            if user_id not in self.user_queues:
                await self.get_user_queue(user_id)

            async with self.lock:
                self.user_queues[user_id]["criteria"] = new_criteria
                logging.info(
                    f"Criteria for user_id {user_id} updated to: {new_criteria}"
                )
        except Exception as e:
            tb_str = traceback.format_exception(e)
            logging.error("".join(tb_str))
            logging.error(
                f"Failed to set new criteria for user_id: {user_id}. Exception: {e}"
            )

    async def start_populate_task(self, user_id):
        try:
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
                logging.info(
                    f"Populate task for user_id: {user_id} is already running or not ready to be restarted."
                )
        except Exception as e:
            logging.error(
                f"Failed to start populate task for user_id: {user_id}. Exception: {e}",
                exc_info=True,
            )

    async def stop_populate_task(self, user_id):
        # Set the stop flag first to signal the task should stop
        await self.set_stop_flag(user_id, True)

        user_queue_info = self.user_queues.get(user_id)
        if user_queue_info and user_queue_info.get("populate_task"):
            user_queue_info["populate_task"].cancel()  # Request cancellation
            try:
                await user_queue_info[
                    "populate_task"
                ]  # Wait for the task to be cancelled
            except asyncio.CancelledError:
                logging.info(f"Populate task for user_id {user_id} cancelled.")
            finally:
                logging.info(f"Finalizing stop for user_id {user_id}.")

    async def empty_queue(self, user_id):
        try:
            user_queue_info = self.user_queues.get(user_id)
            if user_queue_info:
                queue = user_queue_info["queue"]
                async with self.lock:
                    while not queue.empty():
                        await queue.get()
                    logging.info(f"Movie queue for user_id {user_id} emptied")
        except Exception as e:
            tb_str = traceback.format_exception(e)
            logging.error("".join(tb_str))
            logging.error(f"Error emptying queue for user_id: {user_id}: {e}")

    async def mark_movie_seen(self, user_id, tconst):
        info = self.user_queues.get(user_id)
        if info is None:
            info = await self.get_user_queue(user_id)
        seen = self.user_queues[user_id].setdefault("seen_tconsts", set())
        seen.add(tconst)

    async def reset_seen_movies(self, user_id):
        if user_id in self.user_queues:
            self.user_queues[user_id]["seen_tconsts"] = set()

    async def dequeue_movie(self, user_id):
        """Retrieve a movie from the user's queue."""
        user_queue = await self.get_user_queue(user_id)
        movie = await user_queue.get()
        return movie

    async def populate(self, user_id, completion_event=None):
        max_queue_size = self.queue_size
        try:
            while True:
                try:
                    user_queue = await self.get_user_queue(user_id)
                    current_queue_size = user_queue.qsize()

                    if current_queue_size <= 1:
                        if await self.check_stop_flag(user_id):
                            logging.info(
                                f"Abort loading more movies for user_id: {user_id} due to stop signal."
                            )
                            break

                        logging.info(
                            f"Queue size below threshold for user_id: {user_id}, loading more movies..."
                        )
                        await self.load_movies_into_queue(user_id)
                    elif current_queue_size >= max_queue_size:
                        # Queue is full; sleep briefly to yield control
                        await asyncio.sleep(0.5)
                    else:
                        # Avoid busy waiting when queue is partially filled
                        await asyncio.sleep(0.5)

                except asyncio.CancelledError:
                    logging.info(
                        f"Populate task for user_id: {user_id} has been cancelled."
                    )
                    break
                except Exception as e:
                    logging.exception(
                        f"Exception in populate for user_id: {user_id}: {e}"
                    )
        finally:
            if completion_event:
                completion_event.set()
            logging.info(
                f"Population task for user_id: {user_id} is checking for more work or completing."
            )

    def is_task_running(self, user_id=None):
        """Return True if any populate task (or the specified user's task) is running."""
        if user_id:
            task = self.user_queues.get(user_id, {}).get("populate_task")
            return task is not None and not task.done()

        for info in self.user_queues.values():
            task = info.get("populate_task")
            if task and not task.done():
                return True
        return False

    async def fetch_and_enqueue_movie(self, tconst, user_id):
        try:
            start_time = time.time()  # Measure total execution time

            movie = Movie(tconst, self.db_pool)
            movie_data_tmdb = await movie.get_movie_data()

            fetch_time = time.time() - start_time  # Measure fetch time

            if movie_data_tmdb:
                user_queue = await self.get_user_queue(user_id)
                async with self.lock:
                    info = self.user_queues[user_id]
                    seen = info.setdefault("seen_tconsts", set())
                    queued_ids = {m.get("imdb_id") for m in list(user_queue._queue)}
                    if (
                        not user_queue.full()
                        and tconst not in queued_ids
                        and tconst not in seen
                    ):
                        await user_queue.put(movie_data_tmdb)
                        self.movie_enqueue_count += 1
                        logging.info(
                            f"[{self.movie_enqueue_count}] Enqueued movie '{movie_data_tmdb.get('title')}' with tconst: {tconst} for user_id: {user_id} "
                            f"(fetch time: {fetch_time:.2f}s, total time: {time.time() - start_time:.2f}s)"
                        )

        except Exception as e:
            tb_str = traceback.format_exception(e)
            logging.error("".join(tb_str))
            logging.error(
                f"Error fetching/enqueuing movie {tconst} for user_id: {user_id}: {e}"
            )

    async def load_movies_into_queue(self, user_id):
        start_time = time.time()  # Measure total function time

        try:
            user_criteria = (
                self.user_queues[user_id]["criteria"]
                if user_id in self.user_queues
                and "criteria" in self.user_queues[user_id]
                else {}
            )
            logging.info(
                f"Loading movies into queue for user_id: {user_id} with criteria: {user_criteria}"
            )

            async with current_app.app_context(), httpx.AsyncClient():
                fetch_start_time = time.time()  # Measure movie fetching time
                user_queue = await self.get_user_queue(user_id)
                limit = self.queue_size - user_queue.qsize()
                rows = await self.movie_fetcher.fetch_random_movies(user_criteria, limit)
                fetch_time = time.time() - fetch_start_time

                if rows:
                    logging.debug(
                        f"Fetched {len(rows)} movies for user_id: {user_id} based on criteria: {user_criteria} (fetch time: {fetch_time:.2f}s)"
                    )
                else:
                    logging.warning(
                        f"No movies fetched for user_id: {user_id} with the given criteria: {user_criteria}"
                    )

                tasks = [
                    asyncio.create_task(
                        self.fetch_and_enqueue_movie(row["tconst"], user_id)
                    )
                    for row in rows
                    if row
                ]
                await asyncio.gather(*tasks)

        except Exception as e:
            tb_str = traceback.format_exception(e)
            logging.error("".join(tb_str))
            logging.error(
                f"Error loading movies into queue for user_id: {user_id}: {e}"
            )

        finally:  # Log total time in all cases
            total_time = time.time() - start_time
            logging.info(
                f"Completed loading movies into queue for user_id: {user_id} (total time: {total_time:.2f}s)"
            )

    async def update_criteria_and_reset(self, user_id, new_criteria):
        try:
            # Update the criteria and reset the queue for a specific user
            await self.set_criteria(user_id, new_criteria)
            await self.empty_queue(user_id)

            # Restart the populate task for the user
            user_queue_info = self.user_queues.get(user_id)
            if user_queue_info:
                user_queue_info["populate_task"] = asyncio.create_task(
                    self.populate(user_id)
                )
                logging.info(f"Populate task restarted for user_id: {user_id}")
        except Exception as e:
            tb_str = traceback.format_exception(e)
            logging.error("".join(tb_str))
            logging.error(
                f"Failed to update criteria and reset for user_id: {user_id}. Exception: {e}"
            )


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