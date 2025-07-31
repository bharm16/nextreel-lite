import asyncio
import logging
import sys
import time
import uuid

from redis import asyncio as aioredis
from quart import Quart, request, redirect, url_for, session, render_template, g
from quart_session import Session

import settings
from logging_config import setup_logging
from middleware import add_correlation_id
from movie_service import MovieManager

import os
from local_env_setup import setup_local_environment

setup_logging(log_level=logging.DEBUG)
logger = logging.getLogger(__name__)


# Automatically set up local environment if not in production
if os.getenv("FLASK_ENV") != "production":
    setup_local_environment()



def create_app():
    app = Quart(__name__)
    app.config.from_object(settings.Config)



    app.config['SESSION_TYPE'] = 'redis'

    @app.before_serving
    async def setup_redis():
        if os.getenv("FLASK_ENV") == "production":
            # Production Redis configuration
            cache = await aioredis.Redis(
                host=os.getenv("UPSTASH_REDIS_HOST"),
                port=int(os.getenv("UPSTASH_REDIS_PORT")),
                password=os.getenv("UPSTASH_REDIS_PASSWORD"),
                ssl=True
            )
        else:
            # Development Redis configuration
            cache = await aioredis.Redis(
                host="localhost",
                port=6379,  # Default Redis port for local development
                ssl=False
            )
        app.config['SESSION_REDIS'] = cache
        Session(app)

    movie_manager = MovieManager(settings.Config.get_db_config())

    @app.before_request
    async def before_request():
        try:
            # Ensure correlation ID is set for logging
            await add_correlation_id()
            # Check if 'user_id' is not in the session
            if 'user_id' not in session:
                # Generate a new UUID if not present and add it to the session
                session['user_id'] = str(uuid.uuid4())
                logger.info(
                    "New user_id generated: %s. Correlation ID: %s",
                    session['user_id'],
                    g.correlation_id,
                )

                # Define default criteria or fetch it from somewhere if you have personalized criteria logic
                default_criteria = {"min_year": 1900, "max_year": 2023, "min_rating": 7.0,
                                    "genres": ["Action", "Comedy"]}

                # Add user with criteria
                await movie_manager.add_user(session['user_id'], default_criteria)

                # Assuming movie_manager provides access to the MovieQueue instance,
                # start preloading movies into the user's queue
                # This line is the main addition to integrate start_populate_task
                await movie_manager.movie_queue_manager.start_populate_task(session['user_id'])

            else:
                logger.debug("Existing user_id found: %s", session['user_id'])

            # Calculate and log request size
            req_size = sys.getsizeof(await request.get_data())
            logger.debug(
                "Request Size: %d bytes. Correlation ID: %s",
                req_size,
                g.correlation_id,
            )
        except Exception as e:
            logger.error("Error in session management: %s", e)

    # Set up Redis for session management using aioredis

    @app.before_serving
    async def startup():
        await movie_manager.start()

    @app.route('/movie/<tconst>')
    async def movie_detail(tconst):
        # Extract user_id from the session
        user_id = session.get('user_id')

        # Pass the user_id along with the tconst to the render_movie_by_tconst method
        logger.info(
            "Fetching movie details for tconst: %s, user_id: %s. Correlation ID: %s",
            tconst,
            user_id,
            g.correlation_id,
        )
        return await movie_manager.render_movie_by_tconst(user_id, tconst, template_name='movie.html')

    @app.route('/')
    async def home():
        user_id = session.get('user_id')
        return await movie_manager.home(user_id)

    @app.route('/movie/<slug>')
    async def movie_details(slug):
        user_id = session.get('user_id')
        logger.info(
            "Fetching movie details for slug: %s, user_id: %s. Correlation ID: %s",
            slug,
            user_id,
            g.correlation_id,
        )
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
        logger.debug(
            "Requesting next movie for user_id: %s. Correlation ID: %s",
            user_id,
            g.correlation_id,
        )

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
                logger.debug("No movies available, waiting for %s seconds before retrying...", wait_seconds)
                await asyncio.sleep(wait_seconds)  # Wait before retrying

            # Check if we should trigger a movie queue population
            if session['failed_attempts'] >= 4:
                logger.info(
                    "Failed attempts threshold reached. Triggering movie queue population. Correlation ID: %s",
                    g.correlation_id,
                )
                await movie_manager.movie_queue_manager.start_populate_task(session['user_id'])

                session['failed_attempts'] = 0  # Optionally reset failed attempts

        # After 30 seconds, if no movie is found, log a message and return a custom response
        logger.warning(
            "No more movies available after trying for 30 seconds. Correlation ID: %s",
            g.correlation_id,
        )
        return 'No more movies available. Please try again later.', 200

    @app.route('/previous_movie', methods=['GET', 'POST'])
    # @cpu_profile  # Apply CPU profiling
    # @memory_profile  # Apply memory profiling
    async def previous_movie():
        user_id = session.get('user_id')
        logger.debug(
            "Requesting previous movie for user_id: %s. Correlation ID: %s",
            user_id,
            g.correlation_id,
        )
        response = await movie_manager.previous_movie(user_id)
        return response if response else ('No previous movies', 200)

    @app.route('/setFilters')
    async def set_filters():
        user_id = session.get('user_id')  # Extract user_id from session
        current_filters = session.get('current_filters', {})  # Retrieve current filters from session

        start_time = time.time()  # Capture start time for operation
        logger.info(
            "Starting to set filters for user_id: %s with current filters: %s. Correlation ID: %s",
            user_id,
            current_filters,
            g.correlation_id,
        )


        try:
            # Pass current_filters to the template
            response = await render_template('set_filters.html', current_filters=current_filters)

            # Log the successful completion and time taken
            elapsed_time = time.time() - start_time
            logger.info(
                "Completed setting filters for user_id: %s in %.2f seconds. Correlation ID: %s",
                user_id,
                elapsed_time,
                g.correlation_id,
            )

            return response
        except Exception as e:
            # Exception logging with detailed context
            logger.error("Error setting filters for user_id: %s, Error: %s", user_id, e)
            raise  # Re-raise the exception or handle it as per your error handling policy

    @app.route('/filtered_movie', methods=['POST'])
    # @cpu_profile  # Apply CPU profiling
    # @memory_profile  # Apply memory profiling
    async def filtered_movie_endpoint():
        user_id = session.get('user_id')  # Extract user_id from session
        form_data = await request.form  # Await the form data

        # Store form data in session for persistence
        session['current_filters'] = form_data.to_dict()

        start_time = time.time()  # Capture start time for operation
        logger.info(
            "Starting filtering movies for user_id: %s with form data: %s. Correlation ID: %s",
            user_id,
            form_data,
            g.correlation_id,
        )


        try:
            # Here, you can log before each significant operation to see its duration
            filter_start_time = time.time()
            # Simulate processing and filtering
            await asyncio.sleep(5)  # Simulate some async operation
            filter_elapsed_time = time.time() - filter_start_time
            logger.debug(
                "Simulated filtering operation took %.2f seconds",
                filter_elapsed_time,
            )

            # Before calling the movie_manager's filtered_movie method, log the start time
            movie_filter_start_time = time.time()
            response = await movie_manager.filtered_movie(user_id, form_data)
            movie_filter_elapsed_time = time.time() - movie_filter_start_time
            logger.info(
                "movie_manager.filtered_movie operation took %.2f seconds. Correlation ID: %s",
                movie_filter_elapsed_time,
                g.correlation_id,
            )

            # Log the successful completion and time taken
            elapsed_time = time.time() - start_time
            logger.info(
                "Completed filtering movies for user_id: %s in %.2f seconds. Correlation ID: %s",
                user_id,
                elapsed_time,
                g.correlation_id,
            )

            return response
        except Exception as e:
            # Exception logging with detailed context
            logger.error("Error filtering movies for user_id: %s, Error: %s", user_id, e)
            raise  # Re-raise the exception or handle it as per your error handling policy

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
        logger.info(
            "New user handled with user_id: %s. Correlation ID: %s",
            user_id,
            g.correlation_id,
        )

        # Redirect to the home page or another appropriate page
        return redirect(url_for('home'))

    return app


def get_current_user_id():
    # Retrieve user_id from session or another source
    user_id = session.get('user_id')
    return user_id


app = create_app()


# Apply middleware for correlation ID via the before_request defined in create_app

# @app.route("/")
# async def hello():
#     1/0  # raises an error
#     return {"hello": "world"}
#

if __name__ == "__main__":
    app.run()

