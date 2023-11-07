import time
from queue import Queue

from flask import render_template, redirect, url_for

from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from scripts.tmdb_data import get_backdrop_image_for_home


class MovieManager:
    def __init__(self, db_config):
        self.movie_fetcher = ImdbRandomMovieFetcher(db_config)
        self.criteria = {}
        self.movie_queue = Queue(maxsize=15)
        self.movie_queue_manager = MovieQueue(db_config, self.movie_queue)
        self.future_movies_stack = []
        self.previous_movies_stack = []
        self.current_displayed_movie = None
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = get_backdrop_image_for_home(self.default_movie_tmdb_id)

    from flask import redirect, url_for

    # ... rest of your code ...

    def fetch_and_render_movie(self, template_name='movie.html'):
        while self.current_displayed_movie is None or 'backdrop_path' not in self.current_displayed_movie or not \
        self.current_displayed_movie['backdrop_path']:
            if self.movie_queue.empty():
                # Redirect to a different endpoint if the queue is empty
                # This endpoint should handle rendering or further redirection as needed
                print("Queue is empty, and no current movie is displayed with a valid backdrop image.")
                return redirect(url_for('movie'))  # 'no_movie_endpoint' is an example endpoint name

            # Get the next movie from the queue
            self.current_displayed_movie = self.movie_queue.get()
            print(f"Fetched new movie: {self.current_displayed_movie['title']}")

            # If the fetched movie has a backdrop, break the loop and proceed to render or redirect
            if 'backdrop_path' in self.current_displayed_movie and self.current_displayed_movie['backdrop_path']:
                break
            else:
                # Log the movie that was skipped because it lacked a backdrop
                print(f"Skipping movie '{self.current_displayed_movie['title']}' due to missing backdrop image.")
                self.current_displayed_movie = None  # Reset to force the while loop to continue

        # Now we are sure we have a movie with a backdrop image, render the template with the movie object
        return render_template(template_name,
                               movie=self.current_displayed_movie,
                               previous_count=len(self.previous_movies_stack))

    def next_movie(self):
        if self.current_displayed_movie:
            self.previous_movies_stack.append(self.current_displayed_movie)
            print(f"Moved current movie to previous stack: {self.current_displayed_movie['title']}")

        if self.future_movies_stack:
            self.current_displayed_movie = self.future_movies_stack.pop()
            print(f"Retrieved next movie from future stack: {self.current_displayed_movie['title']}")
        elif not self.movie_queue.empty():
            self.current_displayed_movie = self.movie_queue.get()
            print(f"Fetched next movie from queue: {self.current_displayed_movie['title']}")
        else:
            self.current_displayed_movie = None
            print("No movies in future stack and queue is empty.")

        return self.fetch_and_render_movie()

    def previous_movie(self):
        if self.current_displayed_movie:
            self.future_movies_stack.append(self.current_displayed_movie)
            print(f"Moved current movie to future stack: {self.current_displayed_movie['title']}")

        if self.previous_movies_stack:
            self.current_displayed_movie = self.previous_movies_stack.pop()
            print(f"Retrieved previous movie: {self.current_displayed_movie['title']}")
        else:
            print("No previous movies to retrieve.")

        return self.fetch_and_render_movie()

    def update_criteria(self, new_criteria):
        self.criteria = new_criteria
        self.movie_queue_manager.update_criteria_and_reset(self.criteria)
        print("Criteria updated:", self.criteria)

    def set_filters(self):
        start_time = time.time()
        print("Entering setFilters")

        self.movie_queue_manager.stop_populate_thread()
        print(f"Stopping populate thread took {time.time() - start_time} seconds")

        self.movie_queue_manager.empty_queue()
        print(f"Emptying queue took {time.time() - start_time} seconds")

        self.current_displayed_movie = None
        print("Current displayed movie has been reset due to filter change.")

        print(f"Total time taken for setFilters: {time.time() - start_time} seconds")
        return render_template('set_filters.html')

    def home(self):
        return render_template('home.html', default_backdrop_url=self.default_backdrop_url)

    def filtered_movie(self, form_data):
        # Extract new filter criteria from the form
        new_criteria = extract_movie_filter_criteria(form_data)

        # Update the instance criteria with the new filters
        self.criteria = new_criteria

        # Update the existing movie queue manager with the new filter criteria
        self.movie_queue_manager.update_criteria_and_reset(self.criteria)

        # For debugging purposes, print out the new criteria and check if the thread is alive
        print("Extracted criteria:", new_criteria)
        print("Is populate_thread alive after updating criteria?", self.movie_queue_manager.is_thread_alive())

        # We give the thread a few seconds to populate the queue with movies that match the new criteria
        time.sleep(5)

        # Return the rendered movie
        return self.fetch_and_render_movie()
