import logging
import queue
import time
from queue import Queue

import tmdb
from flask import Flask, render_template, request
from flask_login import current_user

import config
from config import Config

from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from scripts.tmdb_data import get_backdrop_image_for_home

# Initialize Flask app with the imported configurations
app = Flask(__name__)
app.config.from_object(Config)

# Apply the secret key from config
app.secret_key = app.config['SECRET_KEY']

# Set TMDb API key from config
tmdb.API_KEY = app.config['TMDB_API_KEY']

# Use the database configurations from config.py
# user_db_config = Config.USER_DB_CONFIG
stackhero_db_config = Config.STACKHERO_DB_CONFIG
# You might need to replace 'your_database_name' with the actual name of the database you want to connect to.


default_movie_tmdb_id = 62
default_backdrop_url = get_backdrop_image_for_home(default_movie_tmdb_id)


# Set it as a global template variable
@app.context_processor
def inject_default_backdrop_url():
    return dict(default_backdrop_url=default_backdrop_url)


# Define global variables to hold the movie fetcher and criteria
global_movie_fetcher = ImdbRandomMovieFetcher(stackhero_db_config)
global_criteria = {}  # Start with empty criteria; can be updated dynamically

# Set your TMDb API key

# Initialize movie queue and its manager
movie_queue = Queue(maxsize=25)
movie_queue_manager = MovieQueue(stackhero_db_config, movie_queue)

# Optionally check that the thread is alive
print("Is populate_thread alive?", movie_queue_manager.is_thread_alive())

# Initialize two lists to act as stacks for previous and future movies

future_movies_stack = []
previous_movies_stack = []
current_displayed_movie = None


# Helper function to fetch and render a movie
def fetch_and_render_movie():
    global current_displayed_movie, previous_movies_stack
    if current_displayed_movie is None and not movie_queue.empty():
        current_displayed_movie = movie_queue.get()
        print(f"Fetched new movie: {current_displayed_movie['title']}")
    elif current_displayed_movie is None:
        print("Queue is empty, and no current movie is displayed.")
        # Handle the empty queue scenario, perhaps by rendering a different template or showing a message
        return render_template('no_movie.html')

    return render_template('movie.html',
                           movie=current_displayed_movie,
                           previous_count=len(previous_movies_stack))


# Route for displaying the current movie or the next movie in the queue
@app.route('/movie')
def movie():
    return fetch_and_render_movie()


# Route for displaying the next movie
@app.route('/next_movie', methods=['GET', 'POST'])
def next_movie():
    global current_displayed_movie, previous_movies_stack, future_movies_stack
    if current_displayed_movie:
        previous_movies_stack.append(current_displayed_movie)
        print(f"Moved current movie to previous stack: {current_displayed_movie['title']}")

    if future_movies_stack:
        # If there are movies in the future stack, use the last one as the next movie
        current_displayed_movie = future_movies_stack.pop()
        print(f"Retrieved next movie from future stack: {current_displayed_movie['title']}")
    elif not movie_queue.empty():
        # Otherwise, fetch the next movie from the queue
        current_displayed_movie = movie_queue.get()
        print(f"Fetched next movie from queue: {current_displayed_movie['title']}")
    else:
        # If both the future stack and the queue are empty, handle that scenario
        current_displayed_movie = None
        print("No movies in future stack and queue is empty.")
        # You could render a different template or show a message here as well

    return fetch_and_render_movie()


# Route for moving to the previous movie
@app.route('/previous_movie', methods=['GET', 'POST'])
def previous_movie():
    global current_displayed_movie, previous_movies_stack, future_movies_stack
    if current_displayed_movie:
        # Move the current movie to the future stack when going back to a previous movie
        future_movies_stack.append(current_displayed_movie)
        print(f"Moved current movie to future stack: {current_displayed_movie['title']}")

    if previous_movies_stack:
        # Retrieve the last movie from the previous stack
        current_displayed_movie = previous_movies_stack.pop()
        print(f"Retrieved previous movie: {current_displayed_movie['title']}")
    else:
        print("No previous movies to retrieve.")
        # Handle the case where there are no previous movies

    return fetch_and_render_movie()


@app.route('/filtered_movie', methods=['POST'])
def filtered_movie_endpoint():
    global movie_queue_manager  # Ensure you're modifying the global instance

    # Extract new filter criteria from the form
    new_criteria = extract_movie_filter_criteria(request.form)

    # Update global criteria
    global_criteria = new_criteria

    # Stop the existing movie queue thread before creating a new one
    if movie_queue_manager.is_thread_alive():
        movie_queue_manager.stop_populate_thread()  # Signal the thread to stop
        movie_queue_manager.populate_thread.join()  # Wait for the thread to finish

    # Empty the queue to clear any movies from the old criteria
    movie_queue_manager.empty_queue()

    # Initialize a new movie queue and its manager with the updated filter criteria
    movie_queue_manager = MovieQueue(stackhero_db_config, movie_queue, global_criteria)

    # Debugging
    print("Extracted criteria:", new_criteria)
    print("Is populate_thread alive after restarting?", movie_queue_manager.is_thread_alive())

    # Wait for a few seconds to give the thread some time to populate the queue
    time.sleep(5)

    return fetch_and_render_movie(movie_queue, previous_movies_stack, criteria=new_criteria)


@app.route('/')
def home():
    return render_template('home.html')


# Declare a global variable to store the last displayed movie
global last_displayed_movie




import time


@app.route('/setFilters')
def set_filters():
    start_time = time.time()
    print("Entering setFilters")

    movie_queue_manager.stop_populate_thread()
    # Log the time taken to stop the populate thread
    print(f"Stopping populate thread took {time.time() - start_time} seconds")

    movie_queue_manager.empty_queue()
    # Log the time taken to empty the queue
    print(f"Emptying queue took {time.time() - start_time} seconds")

    # Add more logging statements for other operations...

    print(f"Total time taken for setFilters: {time.time() - start_time} seconds")
    # Render the filter settings template
    return render_template('set_filters.html')


if __name__ == "__main__":
    # Run the Flask app in debug mode (change this in production)
    app.run(debug=True)
