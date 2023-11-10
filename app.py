from quart import Quart, request, redirect, url_for
import config
from movie_manager import MovieManager

def create_app():
    # Create the Quart application
    app = Quart(__name__)
    app.config.from_object(config.Config)

    # Initialize the MovieManager with the database configuration
    movie_manager = MovieManager(config.Config.STACKHERO_DB_CONFIG)

    @app.route('/')
    async def home():
        # Use the home method from MovieManager
        return await movie_manager.home()

    @app.route('/movie')
    async def movie():
        # Fetch and render a movie
        movie_or_none = await movie_manager.fetch_and_render_movie()
        if movie_or_none is None:
            # Redirect to a fallback route or return a message when the queue is empty
            return redirect(url_for('home'))
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

    # Add other routes and functionalities as needed

    return app

# This is the entry point for running the app with an ASGI server like Hypercorn
app = create_app()
