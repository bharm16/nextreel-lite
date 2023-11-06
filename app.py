from flask import Flask, request, render_template
import config
from movie_manager import MovieManager  # Assuming you have moviemanager.py with the MovieManager class

# Initialize the Flask application
app = Flask(__name__)
app.config.from_object(config.Config)

# Create an instance of MovieManager with the database configuration
movie_manager = MovieManager(config.Config.STACKHERO_DB_CONFIG)

# Set up your Flask routes
@app.route('/')
def home():
    # Use the home method from MovieManager
    return movie_manager.home()

@app.route('/movie')
def movie():
    # Display the current movie or the next movie in the queue
    return movie_manager.fetch_and_render_movie()

@app.route('/next_movie', methods=['GET', 'POST'])
def next_movie():
    # Display the next movie
    return movie_manager.next_movie()

@app.route('/previous_movie', methods=['GET', 'POST'])
def previous_movie():
    # Go back to the previous movie
    return movie_manager.previous_movie()

@app.route('/setFilters')
def set_filters():
    # Set or update filters
    return movie_manager.set_filters()

@app.route('/filtered_movie', methods=['POST'])
def filtered_movie_endpoint():
    # Handle the filtered movie request
    return movie_manager.filtered_movie(request.form)

# ... Include additional routes and logic as needed ...

if __name__ == "__main__":
    # Run the Flask app
    app.run(debug=True)
