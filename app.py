import asyncio
import logging
import sys
import time
import uuid

import aioredis
from quart import Quart, request, redirect, url_for, session, render_template
from quart_session import Session

import config
from movie_manager import MovieManager
from scripts import movie_queue
from scripts.set_filters_for_nextreel_backend import extract_movie_filter_criteria

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

                # Define default criteria or fetch it from somewhere if you have personalized criteria logic
                default_criteria = {"min_year": 1900, "max_year": 2023, "min_rating": 7.0,
                                    "genres": ["Action", "Comedy"]}

                # Add user with criteria
                await movie_manager.add_user(session['user_id'], default_criteria)

                # Assuming movie_manager provides access to the MovieQueue instance,
                # start preloading movies into the user's queue
                # This line is the main addition to integrate start_populate_task
                movie_manager.movie_queue_manager.start_populate_task(session['user_id'])

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

    @app.route('/movie/<slug>')
    async def movie_details(slug):
        user_id = session.get('user_id')
        logging.info(f"Fetching movie details for slug: {slug} and user_id: {user_id}")

        # Fetch movie details by slug
        movie_details = await movie_manager.get_movie_by_slug(user_id, slug)

        if movie_details:
            # Render the movie
            return await movie_manager.fetch_and_render_movie(movie_details, user_id)
        else:
            # If no movie is found for the slug, return a 404 error
            return 'Movie not found', 404

    @app.route('/next_movie', methods=['GET', 'POST'])
    async def next_movie():
        user_id = session.get('user_id')
        logging.info(f"Requesting next movie for user_id: {user_id}")

        if 'failed_attempts' not in session:
            session['failed_attempts'] = 0

        total_duration = 30  # Total duration to keep trying in seconds
        wait_seconds = 5  # Seconds to wait between attempts
        start_time = time.time()  # Capture start time

        while (time.time() - start_time) < total_duration:
            response = await movie_manager.next_movie(user_id)
            if response:
                session['failed_attempts'] = 0  # Reset failed attempts on success
                return response
            else:
                session['failed_attempts'] += 1
                logging.info(
                    f"No movies available, waiting for {wait_seconds} seconds before retrying...")
                await asyncio.sleep(wait_seconds)  # Wait before retrying

            # Check if we should trigger a movie queue population
            if session['failed_attempts'] >= 4:
                logging.info("Failed attempts threshold reached. Triggering movie queue population.")
                movie_queue.start_populate_task(user_id)  # Optionally start population task
                session['failed_attempts'] = 0  # Optionally reset failed attempts

        # After 30 seconds, if no movie is found, log a message and return a custom response
        logging.warning("No more movies available after trying for 30 seconds.")
        return 'No more movies available. Please try again later.', 200

    @app.route('/previous_movie', methods=['GET', 'POST'])
    async def previous_movie():
        user_id = session.get('user_id')
        logging.info(f"Requesting previous movie for user_id: {user_id}")
        response = await movie_manager.previous_movie(user_id)
        return response if response else ('No previous movies', 200)

    @app.route('/setFilters')
    async def set_filters():
        user_id = session.get('user_id')  # Extract user_id from session
        # Retrieve current filters from session; if none exist, use an empty dict
        current_filters = session.get('current_filters', {})
        logging.info(f"Setting filters for user_id: {user_id} with current filters: {current_filters}")
        # Pass current_filters to the template
        return await render_template('set_filters.html', current_filters=current_filters)



    # @app.route('/filtered_movie', methods=['POST'])
    # async def filtered_movie_endpoint():
    #     user_id = session.get('user_id')  # Extract user_id from session
    #     logging.info(f"Applying movie filters for user_id: {user_id}")
    #     form_data = await request.form  # Await the form data
    #     # Extract and store filters in session for persistence
    #     session['current_filters'] = extract_movie_filter_criteria(form_data)
    #     return await movie_manager.filtered_movie(user_id, form_data)

    @app.route('/filtered_movie', methods=['POST'])
    async def filtered_movie_endpoint():
        user_id = session.get('user_id')  # Extract user_id from session
        form_data = await request.form  # Await the form data

        # Store form data in session for persistence
        session['current_filters'] = form_data.to_dict()

        # Log and proceed with filtering
        logging.info(f"Applying movie filters for user_id: {user_id}")
        await asyncio.sleep(5)
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
