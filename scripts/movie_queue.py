import os
import queue
import threading
from queue import Queue
import time
from concurrent.futures import ThreadPoolExecutor
import logging

# Configure logging at the start of your script
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG to capture all levels of log messages
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    filename='/Users/bryceharmon/Desktop/logfile.log',  # Set the path to your desired log file
    filemode='a'  # Append mode, which allows logging to be added to the same file across different runs
)

# Replace all print statements with logging
logger = logging.getLogger(__name__)

from config import Config
from scripts.movie import get_tmdb_id_by_tconst, Movie
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher
from scripts.tmdb_data import get_movie_info_by_tmdb_id

# Set the working directory to the parent directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)
logger.debug(f"Current working directory after change: {os.getcwd()}")

class MovieQueue:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not isinstance(cls._instance, cls):
            cls._instance = super(MovieQueue, cls).__new__(cls)
            logger.debug("Creating a new instance of MovieQueue")
        return cls._instance

    def __init__(self, db_config, queue, criteria=None):
        if not hasattr(self, '_initialized'):
            self.db_config = db_config
            self.queue = queue
            self.movie_fetcher = ImdbRandomMovieFetcher(self.db_config)
            self.criteria = criteria or {}
            self.stop_thread = False
            self.lock = threading.Lock()
            logger.info(f"MovieQueue instance created with criteria: {self.criteria}")

            if not hasattr(self, 'populate_thread'):
                self.populate_thread = threading.Thread(target=self.populate)
                self.populate_thread.daemon = True
                self.populate_thread.start()
                logger.debug("Populate thread started")
            self._initialized = True

    def set_criteria(self, new_criteria):
        with self.lock:
            self.criteria = new_criteria
            logger.info(f"MovieQueue criteria updated to: {self.criteria}")

    def stop_populate_thread(self):
        with self.lock:
            self.stop_thread = True
            logger.debug("Signal sent to stop the populate thread")

        self.populate_thread.join()
        logger.debug("Populate thread joined")

    def empty_queue(self):
        with self.lock:
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            logger.info("Movie queue emptied")

    def populate(self):
        while not self.stop_thread:
            try:
                current_queue_size = self.queue.qsize()
                if current_queue_size < 2:
                    logger.debug("Current queue size is below threshold, loading more movies...")
                    self.load_movies_into_queue()
                else:
                    logger.debug(f"Queue size is sufficient: {current_queue_size}")

                time.sleep(1)
            except Exception as e:
                logger.exception(f"Exception occurred in populate: {e}")
                time.sleep(5)

        logger.info("Exiting the populate thread")

    def is_thread_alive(self):
        alive = self.populate_thread.is_alive()
        logger.debug(f"Populate thread alive: {alive}")
        return alive

    def fetch_and_enqueue_movie(self, tconst):
        with self.lock:
            if self.stop_thread:
                logger.debug("Stop thread flag is set, exiting fetch_and_enqueue_movie")
                return
            if self.queue.qsize() >= 10:
                logger.debug("Queue size is at or exceeds the limit, not fetching new movie")
                return

        movie = Movie(tconst, self.db_config)
        movie_data_imdb = movie.get_movie_data()
        tmdb_id = get_tmdb_id_by_tconst(tconst)
        movie_data_tmdb = get_movie_info_by_tmdb_id(tmdb_id)
        movie_data_imdb['backdrop_path'] = movie_data_tmdb.get('backdrop_path', None)

        with self.lock:
            self.queue.put(movie_data_imdb)
            logger.info(f"Enqueued movie '{movie_data_imdb.get('title', 'N/A')}' with tconst: {tconst}")

    def load_movies_into_queue(self):
        rows = self.movie_fetcher.fetch_random_movies25(self.criteria)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self.fetch_and_enqueue_movie, row['tconst']) for row in rows if row]
            for future in futures:
                try:
                    future.result()
                except Exception as e:
                    logger.exception(f"An error occurred when loading movies into queue: {e}")

    def update_criteria_and_reset(self, new_criteria):
        self.set_criteria(new_criteria)
        self.empty_queue()
        self.stop_thread = False
        if not self.populate_thread.is_alive():
            self.populate_thread = threading.Thread(target=self.populate)
            self.populate_thread.daemon = True
            self.populate_thread.start()
            logger.debug("Populate thread restarted")

def main():
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
    movie_queue_manager.set_criteria(criteria)

    time.sleep(5)  # Allow time for the queue to populate

    movie_queue_manager.stop_populate_thread()
    movie_queue_manager.empty_queue()

    logger.info(f"Is the MovieQueue thread still alive? {movie_queue_manager.is_thread_alive()}")

if __name__ == "__main__":
    main()
