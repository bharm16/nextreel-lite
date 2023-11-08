from quart import Quart, request, redirect, url_for

import config
from movie_manager import MovieManager

# Create an instance of MovieManager with the database configuration
movie_manager = MovieManager(config.Config.STACKHERO_DB_CONFIG)


async def create_app():
    # Create the Quart application
    app = Quart(__name__)
    # Load configuration from the config object
    app.config.from_object(config.Config)

    # Start any required asynchronous tasks here
    await movie_manager.start()  # Replace `start` with an appropriate async init function

    # Define your Quart routes within the factory function
    @app.route('/')
    async def home():
        # Use the home method from MovieManager
        return await movie_manager.home()

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


if __name__ == "__main__":
    # Since create_app is an async function, we need to run it with asyncio
    import asyncio
    app = asyncio.run(create_app())
    # Run the Quart app with debug turned on (only for development)
    app.run(port=5000, debug=True)
