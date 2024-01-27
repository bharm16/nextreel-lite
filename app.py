import logging
import uuid

import redis
from quart import Quart, request, redirect, url_for, session
from quart_session import Session

import config
from movie_manager import MovieManager


def create_app():
    app = Quart(__name__)
    app.config.from_object(config.Config)
    app.config['SESSION_TYPE'] = 'redis'
    # app.config['SESSION_URI'] = redis.from_url('redis://localhost:6379')
    Session(app)

    # app.config['SESSION_URI'] = 'redis://:password@localhost:6379'

    # Initialize Session Management

    movie_manager = MovieManager(config.Config.STACKHERO_DB_CONFIG)

    @app.before_request
    async def before_request():
        try:
            if 'user_id' not in session:
                # Generate a new UUID if not present
                session['user_id'] = str(uuid.uuid4())
                logging.info(f"New user_id generated: {session['user_id']}")
            else:
                logging.info(f"Existing user_id found: {session['user_id']}")
        except Exception as e:
            logging.error(f"Error in session management: {e}")

    @app.before_serving
    async def startup():
        await movie_manager.start()

    @app.route('/')
    async def home():
        user_id = session.get('user_id')
        logging.info(f"Accessing home page with user_id: {user_id}")
        return await movie_manager.home()

    @app.route('/movie')
    async def movie():
        logging.info("Fetching a movie")
        movie_or_none = await movie_manager.fetch_and_render_movie()
        if movie_or_none is None:
            logging.warning("Movie queue is empty, redirecting to home")
            return redirect(url_for('home'))
        else:
            return movie_or_none

    @app.route('/next_movie', methods=['GET', 'POST'])
    async def next_movie():
        logging.info("Requesting next movie")
        response = await movie_manager.next_movie()
        return response if response else ('No more movies', 200)

    @app.route('/previous_movie', methods=['GET', 'POST'])
    async def previous_movie():
        logging.info("Requesting previous movie")
        response = await movie_manager.previous_movie()
        return response if response else ('No previous movies', 200)

    @app.route('/setFilters')
    async def set_filters():
        logging.info("Setting filters")
        return await movie_manager.set_filters()

    @app.route('/filtered_movie', methods=['POST'])
    async def filtered_movie_endpoint():
        logging.info("Applying movie filters")
        form_data = await request.form  # Await the form data
        return await movie_manager.filtered_movie(form_data)

    return app


app = create_app()
