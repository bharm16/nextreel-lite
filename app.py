import asyncio
import logging
import signal
import sys
import time
import uuid

from redis import asyncio as aioredis
from quart import Quart, request, redirect, url_for, session, render_template, g


class FixedQuart(Quart):
    """Quart subclass ensuring Flask compatibility keys."""
    default_config = dict(Quart.default_config)
    default_config.setdefault("PROVIDE_AUTOMATIC_OPTIONS", True)
from quart_session import Session

import settings
from logging_config import setup_logging, get_logger
from middleware import add_correlation_id
from movie_service import MovieManager
from session_auth import ensure_session
from session_auth_enhanced import session_security, ensure_secure_session, require_valid_session
from secrets_manager import secrets_manager

import os
from local_env_setup import setup_local_environment

setup_logging(log_level=logging.INFO)
logger = get_logger(__name__)


# Automatically set up local environment if not in production
if os.getenv("FLASK_ENV") != "production":
    setup_local_environment()

def create_app():
    # Validate all secrets at startup
    if not secrets_manager.validate_all_secrets():
        raise RuntimeError("Failed to validate required secrets. Check logs for details.")
    
    app = FixedQuart(__name__)
    app.config.from_object(settings.Config)
    
    # Initialize session security
    session_security.init_app(app)



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
            await add_correlation_id()
            
            # Skip security for static files and health checks
            if request.path.startswith('/static') or request.path in ['/health', '/ready']:
                return
            
            if app.config.get('TESTING'):
                return
            
            # Apply enhanced session security
            ensure_secure_session()
            
            # Legacy session handling for backward compatibility
            ensure_session()
            
            if not session.get('user_id'):
                session['user_id'] = str(uuid.uuid4())
                default_criteria = {"min_year": 1900, "max_year": 2023, "min_rating": 7.0,
                                    "genres": ["Action", "Comedy"]}
                await movie_manager.add_user(session['user_id'], default_criteria)
            elif 'watch_queue' not in session:
                await movie_manager.add_user(session['user_id'], session.get('criteria', {}))
            
            req_size = sys.getsizeof(await request.get_data())
            logger.debug(
                "Request Size: %s bytes. Correlation ID: %s", req_size, g.correlation_id
            )
        except Exception as e:
            logger.error(f"Error in session management: {e}")
            # Don't fail the request, create new session
            session_security.create_session()
    
    # Set security headers
    @app.after_request
    async def set_security_headers(response):
        # HSTS Header (HTTP Strict Transport Security)
        if app.config.get('SESSION_COOKIE_SECURE'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        
        # Additional security headers
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        return response

    # Set up Redis for session management using aioredis

    @app.before_serving
    async def startup():
        await movie_manager.start()

    @app.route('/logout', methods=['POST'])  # Use POST for logout
    async def logout():
        """Securely logout and destroy session."""
        session_security.destroy_session()
        response = redirect(url_for('home'))
        # Clear cookie on client side
        response.set_cookie(
            app.config['SESSION_COOKIE_NAME'],
            '',
            expires=0,
            secure=app.config.get('SESSION_COOKIE_SECURE', False),
            httponly=True,
            samesite='Lax'
        )
        return response
    
    @app.route('/health')
    async def health_check():
        """Health check endpoint for load balancers"""
        return {'status': 'healthy'}, 200
    
    @app.route('/ready')
    async def readiness_check():
        """Readiness check with database connectivity"""
        try:
            # Check database pool health
            metrics = await movie_manager.db_pool.get_metrics()
            
            # Consider unhealthy if circuit breaker is open or too many failures
            if metrics['circuit_breaker_state'] == 'open':
                return {'status': 'not_ready', 'reason': 'database_circuit_breaker_open'}, 503
            
            if metrics['queries_failed'] > 0 and metrics['queries_executed'] > 0:
                failure_rate = metrics['queries_failed'] / metrics['queries_executed']
                if failure_rate > 0.5:  # More than 50% failure rate
                    return {'status': 'not_ready', 'reason': 'high_db_failure_rate'}, 503
            
            return {
                'status': 'ready',
                'database': {
                    'pool_size': metrics['pool_size'],
                    'free_connections': metrics['free_connections'],
                    'circuit_breaker_state': metrics['circuit_breaker_state'],
                    'queries_executed': metrics['queries_executed'],
                    'avg_query_time_ms': metrics['avg_query_time_ms']
                }
            }, 200
            
        except Exception as e:
            return {'status': 'not_ready', 'reason': str(e)}, 503
    
    @app.route('/metrics')
    async def metrics_endpoint():
        """Detailed metrics endpoint for monitoring"""
        try:
            db_metrics = await movie_manager.db_pool.get_metrics()
            return {
                'database': db_metrics,
                'timestamp': time.time()
            }, 200
        except Exception as e:
            return {'error': str(e)}, 500
    @app.route('/movie/<tconst>')
    async def movie_detail(tconst):
        # Extract user_id from the session
        user_id = session.get('user_id')

        # Pass the user_id along with the tconst to the render_movie_by_tconst method
        logger.debug(
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
        logger.debug(
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
        logger.info(f"Requesting next movie for user_id: {user_id}. Correlation ID: {g.correlation_id}")

        response = await movie_manager.next_movie(user_id)
        if response:
            return response

        logger.warning(f"No more movies available. Correlation ID: {g.correlation_id}")
        return 'No more movies available. Please try again later.', 200

    @app.route('/previous_movie', methods=['GET', 'POST'])
    # @cpu_profile  # Apply CPU profiling
    # @memory_profile  # Apply memory profiling
    async def previous_movie():
        user_id = session.get('user_id')
        logger.info(f"Requesting previous movie for user_id: {user_id}. Correlation ID: {g.correlation_id}")
        response = await movie_manager.previous_movie(user_id)
        return response if response else ('No previous movies', 200)

    @app.route('/setFilters')
    async def set_filters():
        user_id = session.get('user_id')  # Extract user_id from session
        current_filters = session.get('current_filters', {})  # Retrieve current filters from session

        start_time = time.time()  # Capture start time for operation
        logger.info(f"Starting to set filters for user_id: {user_id} with current filters: {current_filters}. Correlation ID: {g.correlation_id}")


        try:
            # Pass current_filters to the template
            response = await render_template('set_filters.html', current_filters=current_filters)

            # Log the successful completion and time taken
            elapsed_time = time.time() - start_time
            logger.info(f"Completed setting filters for user_id: {user_id} in {elapsed_time:.2f} seconds. Correlation ID: {g.correlation_id}")

            return response
        except Exception as e:
            # Exception logging with detailed context
            logger.error(f"Error setting filters for user_id: {user_id}, Error: {e}")
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
        logger.info(f"Starting filtering movies for user_id: {user_id} with form data: {form_data}. Correlation ID: {g.correlation_id}")


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
            logger.debug(
                "movie_manager.filtered_movie operation took %.2f seconds. Correlation ID: %s",
                movie_filter_elapsed_time,
                g.correlation_id,
            )

            # Log the successful completion and time taken
            elapsed_time = time.time() - start_time
            logger.info(f"Completed filtering movies for user_id: {user_id} in {elapsed_time:.2f} seconds. Correlation ID: {g.correlation_id}")

            return response
        except Exception as e:
            # Exception logging with detailed context
            logger.error(f"Error filtering movies for user_id: {user_id}, Error: {e}")
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
        await movie_manager.add_user(user_id, criteria)
        logger.info(f"New user handled with user_id: {user_id}. Correlation ID: {g.correlation_id}")

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

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}. Shutting down gracefully...")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    app.run()

