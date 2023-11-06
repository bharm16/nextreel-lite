import os
import queue
import threading
from queue import Queue
import time
from concurrent.futures import ThreadPoolExecutor

from config import Config
from scripts.movie import get_tmdb_id_by_tconst, Movie
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher
from scripts.tmdb_data import get_movie_info_by_tmdb_id

# Set the working directory to the parent directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)
print(f"Current working directory after change: {os.getcwd()}")

class MovieQueue:
    _instance_count = 0  # Class-level variable to count instances

    def __init__(self, db_config, queue, criteria=None):
        self.__class__._instance_count += 1  # Increment the instance count
        self.instance_id = self.__class__._instance_count  # Instance-specific identifier
        self.db_config = db_config
        self.queue = queue
        self.movie_fetcher = ImdbRandomMovieFetcher(self.db_config)
        self.criteria = criteria or {}  # Use the provided criteria or an empty dict
        self.stop_thread = False
        self.lock = threading.Lock()

        # Initialize the populate thread here
        self.populate_thread = threading.Thread(target=self.populate)
        self.populate_thread.daemon = True
        self.populate_thread.start()

    def set_criteria(self, new_criteria):
        self.criteria = new_criteria

    def stop_populate_thread(self):
        with self.lock:
            print(f"MovieQueue instance {self.instance_id}: Stopping the populate thread...")
            self.stop_thread = True
        self.populate_thread.join()

    def empty_queue(self):
        with self.lock:
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            print(f"MovieQueue instance {self.instance_id}: Emptied the movie queue.")

    def populate(self):
        watched_movies = set()
        watchlist_movies = set()
        last_message = ""  # Keep track of the last printed message to avoid repetition

        while not self.stop_thread:
            try:
                current_queue_size = self.queue.qsize()
                current_message = (
                    f"MovieQueue instance {self.instance_id}: Running the populate_movie_queue loop...\n"
                    f"MovieQueue instance {self.instance_id}: Queue size is {'below threshold, loading more movies...' if current_queue_size < 2 else 'sufficient.'}\n"
                    f"MovieQueue instance {self.instance_id} current queue size: {current_queue_size}\n"
                    f"MovieQueue instance {self.instance_id} queue contents: {[item.get('title', 'N/A') for item in list(self.queue.queue)]}"
                )

                # Only print the message if it's different from the last message
                if current_message != last_message:
                    print(current_message)
                    last_message = current_message  # Update the last message

                # Load more movies if the queue size is less than 2
                if current_queue_size < 2:
                    self.load_movies_into_queue(watched_movies, watchlist_movies)

                time.sleep(1)  # Sleep before the next iteration

            except Exception as e:
                error_message = f"MovieQueue instance {self.instance_id}: Exception occurred in populate: {e}"
                # Print the error message only if it's a new message
                if error_message != last_message:
                    print(error_message)
                    last_message = error_message
                time.sleep(5)  # Optionally add a back-off sleep

        print(f"MovieQueue instance {self.instance_id}: Exiting the populate thread...")

    def is_thread_alive(self):
        return self.populate_thread.is_alive()

    def fetch_and_enqueue_movie(self, tconst):
        with self.lock:
            if self.stop_thread:
                return
            if self.queue.qsize() >= 20:
                return

        # Fetch movie data from IMDb
        movie = Movie(tconst, self.db_config)
        movie_data_imdb = movie.get_movie_data()

        # Fetch movie data from TMDb
        tmdb_id = get_tmdb_id_by_tconst(tconst)
        movie_data_tmdb = get_movie_info_by_tmdb_id(tmdb_id)

        # Merge IMDb and TMDb data
        movie_data = {
            'IMDb': movie_data_imdb,
            'TMDb': movie_data_tmdb
        }
        movie_data_imdb['backdrop_path'] = movie_data_tmdb.get('backdrop_path', None)

        with self.lock:
            # Put the IMDb data on the queue
            self.queue.put(movie_data_imdb)
            # Print the title of the movie instead of the tconst
            print(f"MovieQueue instance {self.instance_id}: Enqueued movie '{movie_data_imdb.get('title', 'N/A')}' with tconst: {tconst}")

    def load_movies_into_queue(self, watched_movies, watchlist_movies):
        rows = self.movie_fetcher.fetch_random_movies25(self.criteria)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for row in rows:
                tconst = row['tconst'] if row else None
                if tconst and tconst not in watched_movies and tconst not in watchlist_movies:
                    future = executor.submit(self.fetch_and_enqueue_movie, tconst)
                    futures.append(future)

            for future in futures:
                future.result()

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

    print(f"Is MovieQueue instance {movie_queue_manager.instance_id} thread still alive? {movie_queue_manager.is_thread_alive()}")

if __name__ == "__main__":
    main()
