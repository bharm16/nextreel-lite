import asyncio
import logging
import sys
import uuid

import aioredis
from quart import Quart, request, redirect, url_for, session
from quart_session import Session

import config
from movie_manager import MovieManager
from scripts import movie_queue

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(filename)s - %(funcName)s - %(levelname)s - %(message)s'
)


def create_app():
    app = Quart(__name__)
    app.config.from_object(config.Config)

    # sentry_sdk.init(
    #     dsn="https://72b1a1db2939610adacb6e75b276a17c@o4506655473074176.ingest.sentry.io/4506725607079936",
    #     enable_tracing=True,
    #     integrations=[
    #         QuartIntegration(),
    #         AsyncioIntegration(),
    #     ],
    #     traces_sample_rate=1.0,
    #     # Set profiles_sample_rate to 1.0 to profile 100%
    #     # of sampled transactions.
    #     # We recommend adjusting this value in production.
    #     profiles_sample_rate=1.0,
    # )

    # # Example route to test Sentry
    # @app.route("/hello")
    # async def hello():
    #     1 / 0  # Intentionally cause an error to test Sentry
    #     return {"hello": "world"}

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

    #
    # @app.route('/next_movie', methods=['GET', 'POST'])
    # async def next_movie():
    #     user_id = session.get('user_id')
    #     logging.info(f"Requesting next movie for user_id: {user_id}")
    #
    #     max_attempts = 5  # Max attempts to check for available movies
    #     attempt = 0  # Initial attempt count
    #     wait_seconds = 2  # Seconds to wait between attempts
    #
    #     while attempt < max_attempts:
    #         response = await movie_manager.next_movie(user_id)
    #         if response:
    #             return response
    #         else:
    #             attempt += 1
    #             logging.info(
    #                 f"No movies available, waiting for {wait_seconds} seconds before retrying... (Attempt {attempt}/{max_attempts})")
    #             await asyncio.sleep(wait_seconds)  # Wait for a bit before retrying
    #
    #     # If we reach here, no movies were available after all attempts
    #     logging.warning("No more movies available after multiple attempts, please try again later.")
    #     return ('No more movies', 200)

    @app.route('/next_movie', methods=['GET', 'POST'])
    async def next_movie():
        user_id = session.get('user_id')
        logging.info(f"Requesting next movie for user_id: {user_id}")

        # Initialize failed_attempts in session if not present
        if 'failed_attempts' not in session:
            session['failed_attempts'] = 0

        max_attempts = 5  # Max attempts to check for available movies
        attempt = 0  # Initial attempt count
        wait_seconds = 2  # Seconds to wait between attempts

        while attempt < max_attempts:
            response = await movie_manager.next_movie(user_id)
            if response:
                session['failed_attempts'] = 0  # Reset failed attempts on success
                return response
            else:
                session['failed_attempts'] += 1
                attempt += 1
                logging.info(
                    f"No movies available, waiting for {wait_seconds} seconds before retrying... (Attempt {attempt}/{max_attempts})")
                await asyncio.sleep(wait_seconds)  # Wait before retrying

            # Check if failed attempts threshold is reached
            if session['failed_attempts'] >= 3:
                logging.info("Failed attempts threshold reached. Triggering movie queue population.")
                movie_queue.start_populate_task(user_id)  # Start population task for this user
                session['failed_attempts'] = 0  # Reset failed attempts after starting population

        # If we reach here, no movies were available after all attempts
            # Redirect to home if no movies are available after all attempts
            logging.warning("No more movies available after multiple attempts, redirecting to home.")
            return redirect(url_for('home'))

    @app.route('/previous_movie', methods=['GET', 'POST'])
    async def previous_movie():
        user_id = session.get('user_id')
        logging.info(f"Requesting previous movie for user_id: {user_id}")
        response = await movie_manager.previous_movie(user_id)
        return response if response else ('No previous movies', 200)

    @app.route('/setFilters')
    async def set_filters():
        user_id = session.get('user_id')  # Extract user_id from session
        logging.info(f"Setting filters for user_id: {user_id}")
        # Pass user_id to the set_filters method
        return await movie_manager.set_filters(user_id)

    @app.route('/filtered_movie', methods=['POST'])
    async def filtered_movie_endpoint():
        user_id = session.get('user_id')  # Extract user_id from session
        logging.info(f"Applying movie filters for user_id: {user_id}")
        form_data = await request.form  # Await the form data
        # Pass both user_id and form_data to the filtered_movie method
        return await movie_manager.filtered_movie(user_id, form_data)

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




# @app.route("/")
# async def hello():
#     1/0  # raises an error
#     return {"hello": "world"}
#
# app.run()
#
