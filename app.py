import asyncio
import logging
import signal
import sys
import time
import uuid
import socket
import hashlib
import json
from contextlib import asynccontextmanager
from functools import wraps

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
from session_security_enhanced import (
    EnhancedSessionSecurity, 
    add_security_headers,
    require_secure_session
)
from secrets_manager import secrets_manager

# Import metrics components
from metrics_collector import (
    MetricsCollector, 
    metrics_endpoint,
    setup_metrics_middleware,
    track_request_metrics,
    movie_recommendations_total,
    user_sessions_total,
    user_actions_total
)

import os
from local_env_setup import setup_local_environment
from secure_cache import SecureCacheManager, CacheNamespace

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
    
    # Initialize enhanced session security
    session_security = EnhancedSessionSecurity(app)



    # Session configuration
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Changed from 'Strict' to 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours
    app.config['SESSION_REFRESH_EACH_REQUEST'] = False  # Don't refresh on every request

    @app.before_serving
    async def setup_redis():
        # Initialize secure cache manager
        cache_secret = os.getenv('CACHE_SECRET_KEY', app.config['SECRET_KEY'])
        redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        
        app.secure_cache = SecureCacheManager(
            redis_url=redis_url,
            secret_key=cache_secret,
            enable_monitoring=True
        )
        await app.secure_cache.initialize()
        logger.info("Secure cache manager initialized")
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
    
    # Initialize metrics collector
    metrics_collector = MetricsCollector(
        db_pool=movie_manager.db_pool,
        movie_manager=movie_manager
    )

    @app.before_request
    async def before_request():
        try:
            await add_correlation_id()
            
            # Track request start time for performance monitoring
            g.start_time = time.time()
            
            # Skip security for static files, health checks, and metrics
            skip_paths = ['/static', '/favicon.ico', '/health', '/ready', '/metrics']
            if any(request.path.startswith(path) for path in skip_paths):
                return
            
            if app.config.get('TESTING'):
                return
            
            # Simple session validation - just check if token exists
            # REMOVED ensure_session() which was destroying sessions on every request
            
            # Check if we need to create a new session
            if 'session_token' not in session:
                from session_auth import generate_session_token
                session['session_token'] = generate_session_token()
                session['user_id'] = str(uuid.uuid4())
                session['created_at'] = time.time()
                logger.info(f"Created new session for user: {session['user_id']}")
                
                # Add default criteria
                default_criteria = {"min_year": 1900, "max_year": 2023, "min_rating": 7.0,
                                    "genres": ["Action", "Comedy"]}
                await movie_manager.add_user(session['user_id'], default_criteria)
                
                # Track new user session
                user_sessions_total.inc()
                metrics_collector.track_user_activity(session['user_id'])
            
            # Check session age (optional - basic timeout)
            if 'created_at' in session:
                session_age = time.time() - session['created_at']
                max_age = 24 * 60 * 60  # 24 hours
                if session_age > max_age:
                    # Session too old, create new one
                    session.clear()
                    from session_auth import generate_session_token
                    session['session_token'] = generate_session_token()
                    session['user_id'] = str(uuid.uuid4())
                    session['created_at'] = time.time()
                    logger.info("Session expired, created new session")
            
            # Initialize user in movie manager if needed
            user_id = session.get('user_id')
            if user_id and 'initialized' not in session:
                criteria = session.get('criteria', {"min_year": 1900, "max_year": 2023, 
                                                    "min_rating": 7.0, "genres": ["Action", "Comedy"]})
                await movie_manager.add_user(user_id, criteria)
                session['initialized'] = True
                logger.info(f"Initialized user {user_id} in movie manager")
            
            req_size = sys.getsizeof(await request.get_data())
            logger.debug(
                "Request Size: %s bytes. Correlation ID: %s", req_size, g.correlation_id
            )
        except Exception as e:
            logger.error(f"Error in session management: {e}")
            # Don't fail the request, just log the error
            pass
    
    # Set security headers and performance monitoring
    @app.after_request
    async def set_security_headers(response):
        # Performance monitoring - log slow requests
        if hasattr(g, 'start_time'):
            elapsed = time.time() - g.start_time
            if elapsed > 1.0:  # Log requests taking more than 1 second
                logger.warning(
                    f"Slow request: {request.endpoint} took {elapsed:.2f}s "
                    f"(user: {session.get('user_id')}, correlation: {g.get('correlation_id')})"
                )
            
            # Add timing header for debugging
            response.headers['X-Response-Time'] = f"{elapsed:.3f}"
        
        # Apply enhanced security headers
        return await add_security_headers(response)

    # Set up Redis for session management using aioredis

    @asynccontextmanager
    async def lifespan(app: Quart):
        """Manage app lifecycle - startup and shutdown"""
        # Startup
        logger.info("Starting application lifecycle")
        
        # Initialize database pool and other resources
        try:
            await movie_manager.start()
            logger.info("MovieManager started successfully")
            
            # Start metrics collection
            await metrics_collector.start_collection()
            logger.info("Metrics collection started")
        except Exception as e:
            logger.error(f"Failed to start MovieManager: {e}")
            raise
        
        yield
        
        # Shutdown - Clean up resources properly to avoid event loop errors
        logger.info("Shutting down application lifecycle")
        try:
            # Stop metrics collection first with timeout
            try:
                await asyncio.wait_for(metrics_collector.stop_collection(), timeout=5.0)
                logger.info("Metrics collection stopped")
            except asyncio.TimeoutError:
                logger.warning("Metrics collection stop timed out")
            except Exception as e:
                logger.warning(f"Error stopping metrics collection: {e}")
            
            # Close Redis connections if they exist
            if hasattr(app, 'redis_client') and app.redis_client:
                try:
                    await asyncio.wait_for(app.redis_client.aclose(), timeout=3.0)
                    logger.info("Redis client closed")
                except asyncio.TimeoutError:
                    logger.warning("Redis client close timed out")
                except Exception as e:
                    logger.warning(f"Error closing Redis client: {e}")
            
            # Close MovieManager and database pool properly
            if hasattr(movie_manager, 'close'):
                try:
                    await asyncio.wait_for(movie_manager.close(), timeout=5.0)
                    logger.info("MovieManager closed successfully")
                except asyncio.TimeoutError:
                    logger.warning("MovieManager close timed out")
                except Exception as e:
                    logger.warning(f"Error closing MovieManager: {e}")
            elif hasattr(movie_manager, 'db_pool') and movie_manager.db_pool:
                try:
                    await asyncio.wait_for(movie_manager.db_pool.close_pool(), timeout=5.0)
                    logger.info("Database pool closed successfully")
                except asyncio.TimeoutError:
                    logger.warning("Database pool close timed out")
                except Exception as e:
                    logger.warning(f"Error closing database pool: {e}")
                    
            # Close secure cache if configured
            if hasattr(app, 'secure_cache'):
                try:
                    await asyncio.wait_for(app.secure_cache.close(), timeout=3.0)
                    logger.info("Secure cache closed")
                except asyncio.TimeoutError:
                    logger.warning("Secure cache close timed out")
                except Exception as e:
                    logger.warning(f"Error closing secure cache: {e}")
            
            # Close session Redis if configured
            if 'SESSION_REDIS' in app.config and app.config['SESSION_REDIS']:
                try:
                    redis_conn = app.config['SESSION_REDIS']
                    await asyncio.wait_for(redis_conn.aclose(), timeout=3.0)
                    logger.info("Session Redis closed")
                except asyncio.TimeoutError:
                    logger.warning("Session Redis close timed out")
                except Exception as e:
                    logger.warning(f"Error closing session Redis: {e}")
                    
        except Exception as e:
            logger.error(f"Critical error during shutdown: {e}")
    
    # Apply lifespan to the app
    app.lifespan = lifespan
    
    # Setup metrics middleware
    setup_metrics_middleware(app, metrics_collector)
    
    def cache_response(ttl=60, namespace=CacheNamespace.API):
        """Secure cache decorator for expensive endpoints"""
        def decorator(f):
            @wraps(f)
            async def wrapper(*args, **kwargs):
                # Generate cache key from request
                user_id = session.get('user_id', 'anonymous')
                cache_key = f"{request.endpoint}:{request.args}:{user_id}"
                cache_key = hashlib.md5(cache_key.encode()).hexdigest()
                
                # Try to get from secure cache
                if hasattr(app, 'secure_cache'):
                    try:
                        cached = await app.secure_cache.get(namespace, cache_key)
                        if cached:
                            logger.debug(f"Cache hit for endpoint {request.endpoint}")
                            return cached
                    except Exception as e:
                        logger.warning(f"Cache read failed: {e}")
                
                # Execute function
                result = await f(*args, **kwargs)
                
                # Cache the result securely
                if hasattr(app, 'secure_cache') and result:
                    try:
                        await app.secure_cache.set(namespace, cache_key, result, ttl=ttl)
                    except Exception as e:
                        logger.warning(f"Cache write failed: {e}")
                
                return result
            return wrapper
        return decorator
    
    async def init_redis_pool():
        """Initialize Redis connection pool for better performance"""
        redis_url = os.getenv('UPSTASH_REDIS_URL', 'redis://localhost:6379')
        
        # Create connection pool with optimized settings
        pool = aioredis.ConnectionPool.from_url(
            redis_url,
            max_connections=50,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
            decode_responses=True
        )
        
        return aioredis.Redis(connection_pool=pool)

    @app.before_serving
    async def startup():
        """Warm up connections and caches on startup"""
        logger.info("Starting application warm-up...")
        
        # Initialize Redis pool
        try:
            app.redis_client = await init_redis_pool()
        except Exception as e:
            logger.warning(f"Redis pool initialization failed: {e}")
            app.redis_client = None
        
        # Warm up database connections
        await movie_manager.start()
        warm_up_tasks = []
        for i in range(5):  # Create 5 warm connections
            warm_up_tasks.append(
                movie_manager.db_pool.execute("SELECT 1", fetch='one')
            )
        
        await asyncio.gather(*warm_up_tasks, return_exceptions=True)
        
        logger.info("Application warm-up complete")
    
    @app.after_serving
    async def cleanup():
        """Clean up resources after serving"""
        logger.info("Cleaning up resources after serving...")
        try:
            # Close movie manager first
            if movie_manager:
                await movie_manager.close()
            
            # Close secure cache
            if hasattr(app, 'secure_cache'):
                try:
                    await app.secure_cache.close()
                except Exception as e:
                    logger.warning(f"Error closing secure cache: {e}")
            
            # Close Redis client
            if hasattr(app, 'redis_client') and app.redis_client:
                try:
                    await app.redis_client.aclose()
                except Exception as e:
                    logger.warning(f"Error closing Redis client: {e}")
            
            # Ensure global pool is closed
            from settings import close_pool
            await close_pool()
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    @app.route('/logout', methods=['POST'])  # Use POST for logout
    async def logout():
        """Securely logout and destroy session."""
        await session_security.destroy_session()
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
    
    @app.route('/metrics')
    async def metrics():
        """Prometheus metrics endpoint"""
        return await metrics_endpoint()
    
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
    @cache_response(ttl=300)  # Cache for 5 minutes
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

        # Track movie recommendation
        metrics_collector.track_movie_recommendation('next_movie')
        user_actions_total.labels(action_type='next_movie').inc()

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
            # Direct call to movie_manager without artificial delays
            response = await movie_manager.filtered_movie(user_id, form_data)

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
    # Cancel all tasks and stop the event loop properly
    try:
        loop = asyncio.get_event_loop()
        if loop and loop.is_running():
            # Cancel all running tasks
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            # Stop the loop
            loop.stop()
    except Exception as e:
        logger.error(f"Error during signal handling: {e}")
    sys.exit(0)

def check_port_available(port: int, host: str = '127.0.0.1') -> bool:
    """Check if a port is available for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            sock.close()
            return True
        except OSError:
            return False

def find_available_port(start_port: int = 5000, max_attempts: int = 10, host: str = '127.0.0.1') -> int:
    """Find an available port starting from start_port."""
    for i in range(max_attempts):
        port = start_port + i
        if check_port_available(port, host):
            logger.info(f"Found available port: {port}")
            return port
        else:
            logger.debug(f"Port {port} is already in use")
    
    logger.error(f"Could not find an available port after {max_attempts} attempts")
    sys.exit(1)

if __name__ == "__main__":
    import os
    
    # Get host and port from environment
    host = os.environ.get('HOST', '127.0.0.1')
    default_port = int(os.environ.get('PORT', 5000))
    
    # Setup graceful shutdown handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Find an available port
    port = find_available_port(default_port, max_attempts=10, host=host)
    
    # Run with proper configuration
    try:
        logger.info(f"Starting NextReel-Lite on http://{host}:{port}")
        app.run(
            host=host,
            port=port,
            debug=False,
            use_reloader=False  # Disable reloader to prevent double initialization
        )
    except OSError as e:
        if e.errno == 48:  # Address already in use
            logger.error(f"Port {port} is still in use. Please wait a moment and try again.")
        else:
            logger.error(f"Failed to start server: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.error(f"Application failed to start: {e}")
        sys.exit(1)

