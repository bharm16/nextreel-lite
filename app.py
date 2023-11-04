import time
from queue import Queue

import tmdb
from flask import Flask, render_template, request
from flask_login import current_user

from db_config import db_config
from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from scripts.tmdb_data import get_backdrop_image_for_home

app = Flask(__name__)
app.secret_key = 'some_random_secret_key'  # IMPORTANT: Change this in production


# Your existing database configuration
user_db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'caching_sha2_password',
    'database': 'imdb'
}

# Database connection configuration for stackhero-network.com MySQL instance
stackhero_db_config = {
    'host': '5ehc1n.stackhero-network.com',   # Hostname provided by Stackhero
    'user': 'root',                           # Username for the database
    'password': 'R1Rx8lklCLyb26B787Fr0au4OjuD2jMC',  # Password for the database user
    'database': 'your_database_name',         # The specific database name to connect to
    'port': 3306                              # Default MySQL port number
}

# You might need to replace 'your_database_name' with the actual name of the database you want to connect to.


# Convert database configuration to SQLAlchemy database URI
db_uri = f"mysql://{user_db_config['user']}:{user_db_config['password']}@{user_db_config['host']}/{user_db_config['database']}"

# Configure your app to use MySQL
app.config['SQLALCHEMY_DATABASE_URI'] = db_uri



default_movie_tmdb_id = 62
default_backdrop_url = get_backdrop_image_for_home(default_movie_tmdb_id)


# Set it as a global template variable
@app.context_processor
def inject_default_backdrop_url():
    return dict(default_backdrop_url=default_backdrop_url)


# Define global variables to hold the movie fetcher and criteria
global_movie_fetcher = ImdbRandomMovieFetcher(db_config)
global_criteria = {}  # Start with empty criteria; can be updated dynamically

# Set your TMDb API key
tmdb.API_KEY = '1ce9398920594a5521f0d53e9b33c52f'

# Initialize movie queue and its manager
movie_queue = Queue(maxsize=25)
movie_queue_manager = MovieQueue(db_config, movie_queue)

# Optionally check that the thread is alive
print("Is populate_thread alive?", movie_queue_manager.is_thread_alive())

# Initialize two lists to act as stacks for previous and future movies

future_movies_stack = []
previous_movies_stack = []
current_displayed_movie = None


def fetch_and_render_movie(movie_queue, current_displayed_movie, previous_movies_stack, criteria=None):
    """Fetch a movie from the given queue and render the movie template."""
    # Check if the queue is empty



    # Fetch the next movie from the queue
    current_movie_data = movie_queue.get()

    # Update the global current_displayed_movie
    current_displayed_movie = current_movie_data

    # Append the current displayed movie to the previous_movies_stack
    previous_movies_stack.append(current_movie_data)

    # Render the movie template, also passing the length of previous_movies_stack for UI control
    return render_template('movie.html',
                           movie=current_movie_data,

                           previous_count=len(previous_movies_stack))


@app.route('/movie')
def movie():
    global movie_queue, current_displayed_movie, previous_movies_stack  # Declare global variables
    # Wait for a few seconds to give the thread some time to populate the queue
    return fetch_and_render_movie(movie_queue, current_displayed_movie, previous_movies_stack)


@app.route('/next_movie', methods=['GET', 'POST'])
def next_movie():
    global current_displayed_movie  # Declare global variables

    # Append the current displayed movie to the previous_movies_stack
    if current_displayed_movie is not None:
        previous_movies_stack.append(current_displayed_movie)

    next_movie_data = None

    # Check if future_movies_stack has any movies to go forward to
    if future_movies_stack:
        next_movie_data = future_movies_stack.pop()
    else:
        # If no future movies, get a new movie from the queue
        next_movie_data = movie_queue.get()

    # Update the current displayed movie
    current_displayed_movie = next_movie_data

    # Render the movie template, also passing the length of previous_movies_stack for UI control
    return render_template('movie.html',
                           movie=next_movie_data,
                           current_user=current_user,
                           previous_count=len(previous_movies_stack))


@app.route('/filtered_movie', methods=['POST'])
def filtered_movie_endpoint():
    global movie_queue, current_displayed_movie, previous_movies_stack  # Declare global variables
    global global_movie_fetcher, global_criteria  # Additional global variables

    # Extract new filter criteria from the form
    new_criteria = extract_movie_filter_criteria(request.form)

    # Update global criteria
    global_criteria = new_criteria

    # Initialize a new movie queue and its manager with the updated filter criteria
    movie_queue_manager = MovieQueue(db_config, movie_queue, global_criteria)

    # Debugging
    print("Extracted criteria:", new_criteria)
    movie_queue_manager.is_thread_alive()

    # Wait for a few seconds to give the thread some time to populate the queue
    time.sleep(5)

    return fetch_and_render_movie(movie_queue, current_displayed_movie, previous_movies_stack, criteria=new_criteria)


@app.route('/')
def home():
    return render_template('home.html')


# Declare a global variable to store the last displayed movie
global last_displayed_movie


@app.route('/previous_movie', methods=['GET', 'POST'])
def previous_movie():
    global current_displayed_movie, future_movies_stack  # Declare global variables

    # Append the current displayed movie to the future_movies_stack
    if current_displayed_movie is not None:
        future_movies_stack.append(current_displayed_movie)

    # Pop the previous movie from previous_movies_stack
    previous_movie_data = previous_movies_stack.pop()

    # Update the current displayed movie
    current_displayed_movie = previous_movie_data

    # Render the movie template, also passing the length of previous_movies_stack for UI control
    return render_template('movie.html',
                           movie=previous_movie_data,
                           previous_count=len(previous_movies_stack))


# Route for setting filters
@app.route('/setFilters')
def set_filters():
    print("entering setFilters")
    movie_queue_manager.stop_populate_thread()
    movie_queue_manager.empty_queue()
    movie_queue_manager.is_thread_alive()

    print(f"Current size of the movie queue: {movie_queue_manager.queue.qsize()}")

    # Render the filter settings template
    return render_template('set_filters.html')


if __name__ == "__main__":
    # Run the Flask app in debug mode (change this in production)
    app.run(debug=True)
