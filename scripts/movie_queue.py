import os
import queue
import threading
from queue import Queue
import time
from concurrent.futures import ThreadPoolExecutor

from config import Config
from scripts.movie import Movie
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher
from scripts.tmdb_data import get_tmdb_id_by_tconst, get_movie_info_by_tmdb_id

# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)


class MovieQueue:
    def __init__(self, db_config, queue, criteria=None):
        self.db_config = db_config
        self.queue = queue
        self.movie_fetcher = ImdbRandomMovieFetcher(self.db_config)
        self.criteria = criteria or {}  # Use the provided criteria or an empty dict

        self.stop_thread = False  # Initialize the stop flag
        self.lock = threading.Lock()

        # Initialize the populate thread here
        self.populate_thread = threading.Thread(target=self.populate)
        self.populate_thread.daemon = True  # Set the thread as a daemon
        self.populate_thread.start()

    def set_criteria(self, new_criteria):
        self.criteria = new_criteria

    def stop_populate_thread(self):
        with self.lock:
            print("Stopping the populate thread...")
            self.stop_thread = True
        self.populate_thread.join()  # Wait for the thread to complete

    def empty_queue(self):
        """Empty the movie queue."""
        with self.lock:  # Ensure thread safety
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            print("Emptied the movie queue.")

    def populate(self):
        # Initialize sets for watched_movies and watchlist_movies
        watched_movies = set()
        watchlist_movies = set()

        # Loop infinitely, but check the stop_thread flag at each iteration
        while not self.stop_thread:
            print("Running the populate_movie_queue loop...")

            # Check if the queue size is less than 2 and populate accordingly
            if self.queue.qsize() < 2:
                # print("Fetching 25 movies from IMDb...")
                self.load_movies_into_queue(watched_movies, watchlist_movies)

            # Sleep for 1 second before the next iteration
            time.sleep(1)

        # This print statement will execute when the thread is stopping.
        print("Stopping the populate thread...")

    def is_thread_alive(self):
        return self.populate_thread.is_alive()

    def fetch_and_enqueue_movie(self, tconst):
        """Fetch and enqueue movie data in a thread-safe manner."""
        with self.lock:
            if self.stop_thread:
                # If the stopping flag is set, do not continue fetching
                return
            # Check if the queue size is at capacity
            if self.queue.qsize() >= 10:
                return

        # Fetch the movie data
        movie = Movie(tconst, self.db_config)
        movie_data_imdb = movie.get_movie_data()

        # Fetch additional movie data from TMDb
        tmdb_id = get_tmdb_id_by_tconst(tconst)
        movie_data_tmdb = get_movie_info_by_tmdb_id(tmdb_id)

        # Combine the IMDb and TMDb data
        movie_data = {
            'IMDb': movie_data_imdb,
            'TMDb': movie_data_tmdb
        }
        movie_data_imdb['backdrop_path'] = movie_data_tmdb.get('backdrop_path', None)

        # Enqueue the movie data
        with self.lock:
            self.queue.put(movie_data_imdb)

    def load_movies_into_queue(self, watched_movies, watchlist_movies):
        # Fetch a batch of 25 movies based on the criteria
        rows = self.movie_fetcher.fetch_random_movies25(self.criteria)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for row in rows:
                tconst = row['tconst'] if row else None
                if tconst and (tconst not in watched_movies) and (tconst not in watchlist_movies):
                    # Submit the task to the ThreadPoolExecutor
                    future = executor.submit(self.fetch_and_enqueue_movie, tconst)
                    futures.append(future)

            # Wait for all futures to complete
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

    # Wait for a few seconds to let the populate method do its work
    time.sleep(5)

    # Stop the populate thread
    movie_queue_manager.stop_populate_thread()

    movie_queue_manager.empty_queue()

    # Check if the thread is still alive
    print(f"Is thread still alive? {movie_queue_manager.is_thread_alive()}")


# This ensures the main function is only run when this script is executed, and not if it's imported as a module
if __name__ == "__main__":
    main()
