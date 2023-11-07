from flask import Flask, request, render_template
from quart import Quart, render_template, url_for, redirect

import config

# Import the MovieManager class here to avoid circular imports
from movie_manager import MovieManager

# Create an instance of MovieManager with the database configuration
movie_manager = MovieManager(config.Config.STACKHERO_DB_CONFIG)


def create_app():
    # Create the Flask application
    app = Quart(__name__)
    # Load configuration from the config object
    app.config.from_object(config.Config)

    # Define your Flask routes within the factory function
    @app.route('/')
    async def home():
        # Use the home method from MovieManager
        return await movie_manager.home()

    @app.route('/movie')
    async def movie():
        # Display the current movie or the next movie in the queue
        return await movie_manager.fetch_and_render_movie()

    @app.route('/next_movie', methods=['GET', 'POST'])
    async def next_movie():
        # Display the next movie
        return await movie_manager.next_movie()

    @app.route('/previous_movie', methods=['GET', 'POST'])
    async def previous_movie():
        # Go back to the previous movie
        return await movie_manager.previous_movie()

    @app.route('/setFilters')
    async def set_filters():
        # Set or update filters
        return await movie_manager.set_filters()

    @app.route('/filtered_movie', methods=['POST'])
    async def filtered_movie_endpoint():
        # Handle the filtered movie request
        return await movie_manager.filtered_movie(request.form)

    # ... Include additional routes and logic as needed ...

    return app


if __name__ == "__main__":
    # Use the application factory function to create the app instance
    app = create_app()
    # Run the Flask app with debug turned on (only for development)
    # app.run(debug=True, use_reloader=False)
    app.run()
