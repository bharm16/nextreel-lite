import logging
import sys
import uuid

import aioredis
from quart import Quart, request, redirect, url_for, session
from quart_session import Session

import config
from movie_manager import MovieManager


def create_app():
    app = Quart(__name__)
    app.config.from_object(config.Config)
    app.config['SESSION_TYPE'] = 'redis'

    @app.before_serving
    async def setup_redis():
        # Set up Redis for session management using aioredis
        cache = await aioredis.Redis(
            host="us1-helped-boxer-41842.upstash.io",
            port=41842,
            password="2c0aa963aca84f82a6f822877cbc2ae8",
            ssl=True
        )
        app.config['SESSION_REDIS'] = cache
        Session(app)



    # app.config['SESSION_URI'] = redis.from_url('redis://localhost:6379')
    # app.config['SESSION_URI'] = 'redis://:password@localhost:6379'
    # Initialize Session Management

    movie_manager = MovieManager(config.Config.STACKHERO_DB_CONFIG)

    @app.before_request
    async def before_request():
        try:
            # Check if 'user_id' is not in the session
            if 'user_id' not in session:
                # Generate a new UUID if not present and add it to the session
                session['user_id'] = str(uuid.uuid4())
                logging.info(f"New user_id generated: {session['user_id']}")

                # Create and populate a queue for the new user
                # Define default criteria or fetch it from somewhere if you have personalized criteria logic
                default_criteria = {"min_year": 1900, "max_year": 2023, "min_rating": 7.0,
                                    "genres": ["Action", "Comedy"]}
                await movie_manager.add_user(session['user_id'], default_criteria)
            else:
                logging.info(f"Existing user_id found: {session['user_id']}")

            # Calculate and log request size
            req_size = sys.getsizeof(await request.get_data())
            logging.info(f"Request Size: {req_size} bytes")
        except Exception as e:
            logging.error(f"Error in session management: {e}")

    # @app.before_request
    # async def before_request():
    #     try:
    #         if 'user_id' not in session:
    #             # Generate a new UUID if not present
    #             session['user_id'] = str(uuid.uuid4())
    #             logging.info(f"New user_id generated: {session['user_id']}")
    #         else:
    #             logging.info(f"Existing user_id found: {session['user_id']}")
    #     except Exception as e:
    #         logging.error(f"Error in session management: {e}")
    #
    #     req_size = sys.getsizeof(await request.get_data())
    #     logging.info(f"Request Size: {req_size} bytes")
    #
    #     # Existing user session handling
    #     if 'user_id' not in session:
    #         session['user_id'] = str(uuid.uuid4())
    #         logging.info(f"New user_id generated: {session['user_id']}")
    #     else:
    #         logging.info(f"Existing user_id found: {session['user_id']}")

    # Set up Redis for session management using aioredis

    @app.before_serving
    async def startup():
        await movie_manager.start()

    @app.route('/')
    async def home():
        user_id = session.get('user_id')
        return await movie_manager.home(user_id)

    # @app.route('/movie')
    # async def movie():
    #     logging.info("Fetching a movie")
    #     user_id = session.get('user_id')
    #     current_displayed_movie = movie_manager.get_current_displayed_movie(user_id)
    #
    #     movie_or_none = await movie_manager.fetch_and_render_movie()
    #     if movie_or_none is None:
    #         logging.warning("Movie queue is empty, redirecting to home")
    #         return redirect(url_for('home'))
    #     else:
    #         return movie_or_none

    @app.route('/next_movie', methods=['GET', 'POST'])
    async def next_movie():
        user_id = session.get('user_id')
        logging.info(f"Requesting next movie for user_id: {user_id}")
        response = await movie_manager.next_movie(user_id)
        return response if response else ('No more movies', 200)

    @app.route('/previous_movie', methods=['GET', 'POST'])
    async def previous_movie():
        user_id = session.get('user_id')
        logging.info(f"Requesting previous movie for user_id: {user_id}")
        response = await movie_manager.previous_movie(user_id)
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

    # Usage in a web application context
    # Define a function to get or create user criteria
    def get_user_criteria():
        # Example static criteria, modify as needed
        return {"min_year": 1900, "max_year": 2023, "min_rating": 7.0, "genres": ["Action", "Comedy"]}

        # Route to handle new user access

    @app.route('/handle_new_user')
    async def handle_new_user():
        user_id = session.get('user_id', str(uuid.uuid4()))  # Generate new user_id if not exists
        session['user_id'] = user_id  # Save user_id to session
        criteria = get_user_criteria()  # Get criteria for the user

        # Initialize user's movie queue with criteria in MovieManager
        await movie_manager.movie_queue_manager.add_user(user_id, criteria)
        logging.info(f"New user handled with user_id: {user_id}")

        # Redirect to the home page or another appropriate page
        return redirect(url_for('home'))

    return app


def get_current_user_id():
    # Retrieve user_id from session or another source
    user_id = session.get('user_id')
    return user_id


app = create_app()
