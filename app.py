import asyncio

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

    async def async_create_app():
        # Now you can safely start asynchronous tasks here
        movie_manager = MovieManager(config.Config.STACKHERO_DB_CONFIG)
        await movie_manager.start()  # Replace `start` with an appropriate async init function

        # Define your Flask routes within the factory function
        @app.route('/')
        async def home():
            # Use the home method from MovieManager
            return await movie_manager.home()

        # Quart route handler that uses fetch_and_render_movie
        @app.route('/movie')
        async def movie():
            movie_or_none = await movie_manager.fetch_and_render_movie()
            if movie_or_none is None:
                # Redirect to a fallback route or return a message when the queue is empty
                return redirect(url_for('home'))  # Replace with your actual fallback route
            else:
                # If there's a movie to display, return the rendered template
                return movie_or_none

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

        return app

    return asyncio.run(async_create_app())


if __name__ == "__main__":
    # Use the application factory function to create the app instance
    app = create_app()
    # Run the Flask app with debug turned on (only for development)
    # app.run(debug=True, use_reloader=False)
    app.run()
