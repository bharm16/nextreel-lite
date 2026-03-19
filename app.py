import asyncio
import logging
import signal
import sys
import time
import socket
import os
from contextlib import asynccontextmanager

from redis import asyncio as aioredis
from quart import Quart, request, session, g


class FixedQuart(Quart):
    """Quart subclass ensuring Flask compatibility keys."""
    default_config = dict(Quart.default_config)
    default_config.setdefault("PROVIDE_AUTOMATIC_OPTIONS", True)
from quart_session import Session

import settings
from logging_config import setup_logging, get_logger
from middleware import add_correlation_id
from movie_service import MovieManager
from session_auth import ensure_session, generate_session_token, init_session
from session_keys import USER_ID_KEY
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

from local_env_setup import setup_local_environment
from secure_cache import SecureCacheManager, CacheNamespace
from routes import bp as routes_bp, init_routes

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
    app.config.from_object(settings.Config())

    # Initialize enhanced session security
    session_security = EnhancedSessionSecurity(app)
    # Store on config so routes can access it for logout
    app.config["_session_security"] = session_security

    # Session configuration
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours
    app.config['SESSION_REFRESH_EACH_REQUEST'] = False

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
            redis_host = os.getenv("UPSTASH_REDIS_HOST")
            redis_port = os.getenv("UPSTASH_REDIS_PORT")
            redis_pw = os.getenv("UPSTASH_REDIS_PASSWORD")
            if not redis_host or not redis_port:
                raise RuntimeError(
                    "UPSTASH_REDIS_HOST and UPSTASH_REDIS_PORT must be set in production"
                )
            cache = await aioredis.Redis(
                host=redis_host,
                port=int(redis_port),
                password=redis_pw,
                ssl=True
            )
        else:
            cache = await aioredis.Redis(
                host="localhost",
                port=6379,
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

    # Inject dependencies into routes
    init_routes(movie_manager, metrics_collector)

    @app.before_request
    async def before_request():
        try:
            await add_correlation_id()
            g.start_time = time.time()

            skip_paths = ['/static', '/favicon.ico', '/health', '/ready', '/metrics']
            if any(request.path.startswith(path) for path in skip_paths):
                return

            if app.config.get('TESTING'):
                return

            await init_session(movie_manager, metrics_collector)

            req_size = sys.getsizeof(await request.get_data())
            logger.debug(
                "Request Size: %s bytes. Correlation ID: %s", req_size, g.correlation_id
            )
        except Exception as e:
            logger.error(f"Error in session management: {e}", exc_info=True)
            # Let the request proceed without a session rather than crashing,
            # but surface the error clearly so it can be investigated.
            # Critical auth failures should still propagate.
            if isinstance(e, RuntimeError):
                raise

    @app.after_request
    async def set_security_headers(response):
        if hasattr(g, 'start_time'):
            elapsed = time.time() - g.start_time
            if elapsed > 1.0:
                logger.warning(
                    f"Slow request: {request.endpoint} took {elapsed:.2f}s "
                    f"(user: {session.get(USER_ID_KEY)}, correlation: {g.get('correlation_id')})"
                )
            response.headers['X-Response-Time'] = f"{elapsed:.3f}"

        return await add_security_headers(response)

    @asynccontextmanager
    async def lifespan(app: Quart):
        """Manage app lifecycle - startup and shutdown"""
        logger.info("Starting application lifecycle")

        try:
            await movie_manager.start()
            logger.info("MovieManager started successfully")

            await metrics_collector.start_collection()
            logger.info("Metrics collection started")
        except Exception as e:
            logger.error(f"Failed to start MovieManager: {e}")
            raise

        yield

        logger.info("Shutting down application lifecycle")
        try:
            await _shutdown_resources(app)
        except Exception as e:
            logger.error(f"Critical error during shutdown: {e}")

    app.lifespan = lifespan

    # Setup metrics middleware
    setup_metrics_middleware(app, metrics_collector)

    async def init_redis_pool():
        """Initialize Redis connection pool for better performance"""
        redis_url = os.getenv('UPSTASH_REDIS_URL', 'redis://localhost:6379')

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

        try:
            app.redis_client = await init_redis_pool()
        except Exception as e:
            logger.warning(f"Redis pool initialization failed: {e}")
            app.redis_client = None

        await movie_manager.start()
        warm_up_tasks = []
        for i in range(5):
            warm_up_tasks.append(
                movie_manager.db_pool.execute("SELECT 1", fetch='one')
            )

        await asyncio.gather(*warm_up_tasks, return_exceptions=True)

        logger.info("Application warm-up complete")

    async def _shutdown_resources(app_instance):
        """Shared shutdown logic — called from both lifespan and after_serving."""
        try:
            await asyncio.wait_for(metrics_collector.stop_collection(), timeout=5.0)
            logger.info("Metrics collection stopped")
        except asyncio.TimeoutError:
            logger.warning("Metrics collection stop timed out")
        except Exception as e:
            logger.warning(f"Error stopping metrics collection: {e}")

        if hasattr(app_instance, 'redis_client') and app_instance.redis_client:
            try:
                await asyncio.wait_for(app_instance.redis_client.aclose(), timeout=3.0)
                logger.info("Redis client closed")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Error closing Redis client: {e}")

        if hasattr(movie_manager, 'close'):
            try:
                await asyncio.wait_for(movie_manager.close(), timeout=5.0)
                logger.info("MovieManager closed successfully")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Error closing MovieManager: {e}")

        if hasattr(app_instance, 'secure_cache'):
            try:
                await asyncio.wait_for(app_instance.secure_cache.close(), timeout=3.0)
                logger.info("Secure cache closed")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Error closing secure cache: {e}")

        if 'SESSION_REDIS' in app_instance.config and app_instance.config['SESSION_REDIS']:
            try:
                await asyncio.wait_for(
                    app_instance.config['SESSION_REDIS'].aclose(), timeout=3.0
                )
                logger.info("Session Redis closed")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Error closing session Redis: {e}")

    @app.after_serving
    async def cleanup():
        """Clean up resources after serving"""
        logger.info("Cleaning up resources after serving...")
        await _shutdown_resources(app)

    # Register the Blueprint
    app.register_blueprint(routes_bp)

    return app


def get_current_user_id():
    user_id = session.get(USER_ID_KEY)
    return user_id


app = create_app()


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}. Shutting down gracefully...")
    try:
        loop = asyncio.get_event_loop()
        if loop and loop.is_running():
            # Only stop the loop — Quart's shutdown hooks will handle cleanup
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
    host = os.environ.get('HOST', '127.0.0.1')
    default_port = int(os.environ.get('PORT', 5000))

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    port = find_available_port(default_port, max_attempts=10, host=host)

    try:
        logger.info(f"Starting NextReel-Lite on http://{host}:{port}")
        app.run(
            host=host,
            port=port,
            debug=False,
            use_reloader=False
        )
    except OSError as e:
        import errno
        if e.errno in (errno.EADDRINUSE, 48):  # 48 = macOS, EADDRINUSE = Linux
            logger.error(f"Port {port} is still in use. Please wait a moment and try again.")
        else:
            logger.error(f"Failed to start server: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.error(f"Application failed to start: {e}")
        sys.exit(1)
