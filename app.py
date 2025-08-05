"""Quart application entry point for the NextReel project.

The application is intentionally structured similarly to a classic Flask app
but uses Quart to gain asynchronous view support.  This file wires together
middleware, session handling via Redis, and route definitions that delegate the
heavy lifting to :class:`movie_service.MovieManager`.
"""

import asyncio
import logging
import sys
import time
import uuid

from redis import asyncio as aioredis
from quart import Quart, request, redirect, url_for, session, render_template, g


class FixedQuart(Quart):
    """Quart subclass ensuring Flask compatibility keys."""

    # Quart's ``default_config`` is immutable, so we copy it before updating.
    default_config = dict(Quart.default_config)
    default_config.setdefault("PROVIDE_AUTOMATIC_OPTIONS", True)


from quart_session import Session

import settings
from logging_config import setup_logging, get_logger
from middleware import add_correlation_id
from movie_service import MovieManager

import os
from local_env_setup import setup_local_environment

# Initialise logging early so all subsequent modules use the configured format
setup_logging(log_level=logging.INFO)
logger = get_logger(__name__)


# Automatically set up local environment if not in production.  This allows the
# application to run out of the box during testing without requiring manual
# export of numerous environment variables.
if os.getenv("FLASK_ENV") != "production":
    setup_local_environment()


def create_app():
    """Application factory used by tests and by ``__main__``."""

    app = FixedQuart(__name__)
    app.config.from_object(settings.Config)

    # Quart-Session expects a session type; here we store session data in Redis
    # to support multiple workers in production.
    app.config['SESSION_TYPE'] = 'redis'

    @app.before_serving
    async def setup_redis():
        """Configure Redis connection used for server-side sessions."""

        if os.getenv("FLASK_ENV") == "production":
            # Production Redis configuration (e.g. Upstash).
            cache = await aioredis.Redis(
                host=os.getenv("UPSTASH_REDIS_HOST"),
                port=int(os.getenv("UPSTASH_REDIS_PORT")),
                password=os.getenv("UPSTASH_REDIS_PASSWORD"),
                ssl=True,
            )
        else:
            # Local development uses an unsecured Redis instance.
            cache = await aioredis.Redis(host="localhost", port=6379, ssl=False)

        app.config['SESSION_REDIS'] = cache
        Session(app)

    movie_manager = MovieManager(settings.Config.get_db_config())

    @app.before_request
    async def before_request():
        """Initialise per-request context and anonymous user sessions."""

        try:
            # Generate or propagate a correlation ID so log lines can be traced
            # across services.
            await add_correlation_id()

            if 'user_id' not in session:
                # New visitors are assigned a random UUID and a default set of
                # movie filter criteria.  This keeps the demo experience simple.
                session['user_id'] = str(uuid.uuid4())
                logger.info(
                    f"New user_id generated: {session['user_id']}. Correlation ID: {g.correlation_id}"
                )

                default_criteria = {
                    "min_year": 1900,
                    "max_year": 2023,
                    "min_rating": 7.0,
                    "genres": ["Action", "Comedy"],
                }

                await movie_manager.add_user(session['user_id'], default_criteria)

                # Kick off background queue population for this user so the
                # first request for a movie returns quickly.
                await movie_manager.movie_queue_manager.start_populate_task(
                    session['user_id']
                )

            else:
                logger.debug("Existing user_id found: %s", session['user_id'])

            # Optionally log the size of the incoming request body.  This is
            # mostly useful when debugging or profiling.
            req_size = sys.getsizeof(await request.get_data())
            logger.debug(
                "Request Size: %s bytes. Correlation ID: %s", req_size, g.correlation_id
            )
        except Exception as e:
            logger.error(f"Error in session management: {e}")

    # Set up Redis for session management using aioredis

    @app.before_serving
    async def startup():
        """Initialise background services before the first request."""

        await movie_manager.start()

    @app.route('/movie/<tconst>')
    async def movie_detail(tconst):
        """Display detailed information for a movie given its IMDb ID."""

        user_id = session.get('user_id')
        logger.debug(
            "Fetching movie details for tconst: %s, user_id: %s. Correlation ID: %s",
            tconst,
            user_id,
            g.correlation_id,
        )
        return await movie_manager.render_movie_by_tconst(
            user_id, tconst, template_name='movie.html'
        )

    @app.route('/')
    async def home():
        """Render the home page with a random movie backdrop."""

        user_id = session.get('user_id')
        return await movie_manager.home(user_id)

    @app.route('/movie/<slug>')
    async def movie_details(slug):
        """Lookup a movie by a human readable slug and display it."""

        user_id = session.get('user_id')
        logger.debug(
            "Fetching movie details for slug: %s, user_id: %s. Correlation ID: %s",
            slug,
            user_id,
            g.correlation_id,
        )
        movie_details = await movie_manager.get_movie_by_slug(user_id, slug)

        if movie_details:
            return await movie_manager.fetch_and_render_movie(movie_details, user_id)
        else:
            return 'Movie not found', 404

    @app.route('/next_movie', methods=['GET', 'POST'])
    async def next_movie():
        """Return the next movie from the queue, retrying for a short period."""

        user_id = session.get('user_id')
        logger.info(
            f"Requesting next movie for user_id: {user_id}. Correlation ID: {g.correlation_id}"
        )

        if 'failed_attempts' not in session:
            session['failed_attempts'] = 0

        total_duration = 30  # Total duration to keep trying in seconds
        wait_seconds = 5  # Seconds to wait between attempts
        start_time = time.time()

        while (time.time() - start_time) < total_duration:
            response = await movie_manager.next_movie(user_id)
            if response:
                session['failed_attempts'] = 0
                return response
            else:
                session['failed_attempts'] += 1
                logger.debug(
                    "No movies available, waiting for %s seconds before retrying...",
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)

            if session['failed_attempts'] >= 4:
                logger.info(
                    f"Failed attempts threshold reached. Triggering movie queue population. Correlation ID: {g.correlation_id}"
                )
                await movie_manager.movie_queue_manager.start_populate_task(
                    session['user_id']
                )
                session['failed_attempts'] = 0

        logger.warning(
            f"No more movies available after trying for 30 seconds. Correlation ID: {g.correlation_id}"
        )
        return 'No more movies available. Please try again later.', 200

    @app.route('/previous_movie', methods=['GET', 'POST'])
    # @cpu_profile  # Apply CPU profiling
    # @memory_profile  # Apply memory profiling
    async def previous_movie():
        """Display the previously viewed movie if available."""

        user_id = session.get('user_id')
        logger.info(
            f"Requesting previous movie for user_id: {user_id}. Correlation ID: {g.correlation_id}"
        )
        response = await movie_manager.previous_movie(user_id)
        return response if response else ('No previous movies', 200)

    @app.route('/setFilters')
    async def set_filters():
        """Display filter form preserving previously chosen options."""

        user_id = session.get('user_id')
        current_filters = session.get('current_filters', {})

        start_time = time.time()
        logger.info(
            f"Starting to set filters for user_id: {user_id} with current filters: {current_filters}. Correlation ID: {g.correlation_id}"
        )

        try:
            response = await render_template(
                'set_filters.html', current_filters=current_filters
            )
            elapsed_time = time.time() - start_time
            logger.info(
                f"Completed setting filters for user_id: {user_id} in {elapsed_time:.2f} seconds. Correlation ID: {g.correlation_id}"
            )
            return response
        except Exception as e:
            logger.error(
                f"Error setting filters for user_id: {user_id}, Error: {e}"
            )
            raise

    @app.route('/filtered_movie', methods=['POST'])
    # @cpu_profile  # Apply CPU profiling
    # @memory_profile  # Apply memory profiling
    async def filtered_movie_endpoint():
        """Receive filter form submission and delegate to ``MovieManager``."""

        user_id = session.get('user_id')
        form_data = await request.form

        # Persist the filters in the session so the form can be pre-populated on
        # subsequent visits.
        session['current_filters'] = form_data.to_dict()

        start_time = time.time()
        logger.info(
            f"Starting filtering movies for user_id: {user_id} with form data: {form_data}. Correlation ID: {g.correlation_id}"
        )

        try:
            filter_start_time = time.time()
            await asyncio.sleep(5)  # Simulated async operation
            filter_elapsed_time = time.time() - filter_start_time
            logger.debug(
                "Simulated filtering operation took %.2f seconds",
                filter_elapsed_time,
            )

            movie_filter_start_time = time.time()
            response = await movie_manager.filtered_movie(user_id, form_data)
            movie_filter_elapsed_time = time.time() - movie_filter_start_time
            logger.debug(
                "movie_manager.filtered_movie operation took %.2f seconds. Correlation ID: %s",
                movie_filter_elapsed_time,
                g.correlation_id,
            )

            elapsed_time = time.time() - start_time
            logger.info(
                f"Completed filtering movies for user_id: {user_id} in {elapsed_time:.2f} seconds. Correlation ID: {g.correlation_id}"
            )

            return response
        except Exception as e:
            logger.error(
                f"Error filtering movies for user_id: {user_id}, Error: {e}"
            )
            raise

    def get_user_criteria():
        """Return example criteria for new users.

        In a real application this might consult a database or user preferences.
        Here it simply returns a static dictionary used when creating a new
        anonymous user.
        """

        return {
            "min_year": 1900,
            "max_year": 2023,
            "min_rating": 7.0,
            "genres": ["Action", "Comedy"],
        }

        # Route to handle new user access

    @app.route('/handle_new_user')
    async def handle_new_user():
        """Explicitly create a new user session and redirect home."""

        user_id = session.get('user_id', str(uuid.uuid4()))
        session['user_id'] = user_id
        criteria = get_user_criteria()

        await movie_manager.movie_queue_manager.add_user(user_id, criteria)
        logger.info(
            f"New user handled with user_id: {user_id}. Correlation ID: {g.correlation_id}"
        )

        return redirect(url_for('home'))

    return app


def get_current_user_id():
    """Helper used by tests to access the current user's ID."""

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

