import logging
import secrets

from quart import Quart, request, redirect, url_for, session
from quart_session import Session

import config
from movie_manager import MovieManager


def create_app():
    app = Quart(__name__)
    app.config.from_object(config.Config)

    # Set a random secret key (important for session security)
    app.secret_key = secrets.token_urlsafe(16)
    #
    # Initialize Session
    Session(app)

    movie_manager = MovieManager(config.Config.STACKHERO_DB_CONFIG)

    # Utility function to generate a unique user ID
    def generate_user_id():
        return secrets.token_urlsafe(16)

    @app.before_serving
    async def startup():
        await movie_manager.start_population_task()

    @app.route('/')
    async def home():
        # Ensure user_id is in the session
        user_id = session.get('user_id', secrets.token_urlsafe(16))
        session['user_id'] = user_id
        logging.info(f"Accessing home page for user_id: {user_id}")
        return await movie_manager.home(user_id)

    @app.route('/movie')
    async def movie():
        user_id = session.get('user_id', secrets.token_urlsafe(16))
        session['user_id'] = user_id
        logging.info("Fetching a movie")
        movie_or_none = await movie_manager.fetch_and_render_movie(user_id)
        if movie_or_none is None:
            logging.warning("Movie queue is empty, redirecting to home")
            return redirect(url_for('home'))
        else:
            return movie_or_none

    # @app.route('/movie')
    # async def movie():
    #     logging.info("Fetching a movie")
    #     movie_or_none = await movie_manager.fetch_and_render_movie()
    #     if movie_or_none is None:
    #         logging.warning("Movie queue is empty, redirecting to home")
    #         return redirect(url_for('home'))
    #     else:
    #         return movie_or_none

    @app.route('/next_movie', methods=['GET', 'POST'])
    async def next_movie():
        # Retrieve the user_id from the session
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('home'))  # Redirect to home if the user_id is not found

        logging.info("Requesting next movie")
        response = await movie_manager.next_movie(user_id)  # Pass the user_id
        return response if response else ('No more movies', 200)

    @app.route('/previous_movie', methods=['GET', 'POST'])
    async def previous_movie():
        # Retrieve the user_id from the session
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('home'))  # Redirect to home if the user_id is not found

        logging.info("Requesting previous movie")
        response = await movie_manager.previous_movie(user_id)  # Pass the user_id
        return response if response else ('No previous movies', 200)

    @app.route('/setFilters')
    async def set_filters():
        # Retrieve the user_id from the session
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('home'))  # Redirect to home if the user_id is not found

        logging.info(f"Setting filters for user_id: {user_id}")
        return await movie_manager.set_filters(user_id)  # Pass the user_id

    @app.route('/filtered_movie', methods=['POST'])
    async def filtered_movie_endpoint():
        # Retrieve the user_id from the session
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('home'))  # Redirect to home if the user_id is not found

        logging.info(f"Applying movie filters for user_id: {user_id}")
        form_data = await request.form  # Await the form data
        return await movie_manager.filtered_movie(form_data, user_id)  # Pass the form data and user_id

    return app


app = create_app()
