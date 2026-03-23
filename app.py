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
from quart_session import Session

import settings
from local_env_setup import setup_local_environment
from logging_config import setup_logging, get_logger
from metrics_collector import MetricsCollector, setup_metrics_middleware
from middleware import add_correlation_id
from movie_service import MovieManager
from routes import bp as routes_bp, init_routes
from secrets_manager import secrets_manager
from session_auth import init_session
from session_keys import USER_ID_KEY
from session_security_enhanced import EnhancedSessionSecurity, add_security_headers


class FixedQuart(Quart):
    """Quart subclass ensuring Flask compatibility keys."""
    default_config = dict(Quart.default_config)
    default_config.setdefault("PROVIDE_AUTOMATIC_OPTIONS", True)
from simple_cache import SimpleCacheManager

setup_logging(log_level=logging.INFO)
logger = get_logger(__name__)


# Automatically set up local environment if not in production
if os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production")) != "production":
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

    # Session cookie and lifetime settings are applied by
    # EnhancedSessionSecurity._configure_secure_settings (above).
    # Only set the session backend type here — everything else is
    # owned by the security module to avoid conflicting values.
    app.config['SESSION_TYPE'] = 'redis'

    @app.before_serving
    async def setup_redis():
        # Build a single Redis URL used by all consumers.
        if os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production")) == "production":
            redis_host = os.getenv("UPSTASH_REDIS_HOST")
            redis_port = os.getenv("UPSTASH_REDIS_PORT")
            redis_pw = os.getenv("UPSTASH_REDIS_PASSWORD")
            if not redis_host or not redis_port:
                raise RuntimeError(
                    "UPSTASH_REDIS_HOST and UPSTASH_REDIS_PORT must be set in production"
                )
            redis_url = f"rediss://:{redis_pw}@{redis_host}:{redis_port}"
        else:
            redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')

        # Shared connection pool — one pool, multiple consumers
        shared_pool = aioredis.ConnectionPool.from_url(
            redis_url,
            max_connections=50,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        app.shared_redis_pool = shared_pool

        # 1) Session backend — uses the shared pool
        session_redis = aioredis.Redis(connection_pool=shared_pool)
        app.config['SESSION_REDIS'] = session_redis
        Session(app)

        # 2) Simple cache manager — reuses the shared pool (no extra connection)
        app.secure_cache = SimpleCacheManager(connection_pool=shared_pool)
        await app.secure_cache.initialize()
        logger.info("Redis shared pool, session backend, and simple cache initialized")

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
            logger.error("Error in session management: %s", e, exc_info=True)
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
                    "Slow request: %s took %.2fs (user: %s, correlation: %s)",
                    request.endpoint, elapsed, session.get(USER_ID_KEY), g.get('correlation_id')
                )
            response.headers['X-Response-Time'] = f"{elapsed:.3f}"

        return await add_security_headers(response)

    # ── Scheduled cache refresh ──────────────────────────────────────
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    cache_scheduler = AsyncIOScheduler()

    async def _refresh_movie_caches():
        """Call the stored procedure that refreshes denormalized cache tables."""
        try:
            await movie_manager.db_pool.execute(
                "CALL refresh_movie_caches()", fetch='none'
            )
            logger.info("Scheduled cache refresh completed successfully")
        except Exception as e:
            logger.warning("Scheduled cache refresh failed: %s", e)

    # Run daily at 03:00 UTC
    cache_scheduler.add_job(_refresh_movie_caches, 'cron', hour=3, minute=0)

    @asynccontextmanager
    async def lifespan(app: Quart):
        """Manage app lifecycle - startup and shutdown"""
        logger.info("Starting application lifecycle")

        try:
            await movie_manager.start()
            logger.info("MovieManager started successfully")

            await metrics_collector.start_collection()
            logger.info("Metrics collection started")

            cache_scheduler.start()
            logger.info("Cache refresh scheduler started (daily at 03:00 UTC)")
        except Exception as e:
            logger.error("Failed to start MovieManager: %s", e)
            raise

        yield

        logger.info("Shutting down application lifecycle")
        try:
            cache_scheduler.shutdown(wait=False)
            await _shutdown_resources(app)
        except Exception as e:
            logger.error("Critical error during shutdown: %s", e)

    app.lifespan = lifespan

    # Setup metrics middleware
    setup_metrics_middleware(app, metrics_collector)

    @app.before_serving
    async def startup():
        """Warm up connections on startup.

        NOTE: ``movie_manager.start()`` is called by the lifespan context
        manager — do NOT call it again here to avoid double-initialisation
        of the database pool.
        """
        logger.info("Starting application warm-up...")

        # Warm up DB connections so the first user request doesn't pay the
        # connection establishment cost.
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
            logger.warning("Error stopping metrics collection: %s", e)

        if hasattr(movie_manager, 'close'):
            try:
                await asyncio.wait_for(movie_manager.close(), timeout=5.0)
                logger.info("MovieManager closed successfully")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("Error closing MovieManager: %s", e)

        if hasattr(app_instance, 'secure_cache'):
            try:
                await asyncio.wait_for(app_instance.secure_cache.close(), timeout=3.0)
                logger.info("Secure cache closed")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("Error closing secure cache: %s", e)

        if 'SESSION_REDIS' in app_instance.config and app_instance.config['SESSION_REDIS']:
            try:
                await asyncio.wait_for(
                    app_instance.config['SESSION_REDIS'].aclose(), timeout=3.0
                )
                logger.info("Session Redis closed")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("Error closing session Redis: %s", e)

        # Close the shared Redis connection pool last
        if hasattr(app_instance, 'shared_redis_pool') and app_instance.shared_redis_pool:
            try:
                await asyncio.wait_for(
                    app_instance.shared_redis_pool.disconnect(), timeout=3.0
                )
                logger.info("Shared Redis pool closed")
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("Error closing shared Redis pool: %s", e)

    # NOTE: shutdown is handled exclusively by the lifespan() context manager.
    # A previous @after_serving cleanup() handler was removed to prevent
    # double-close of Redis pools, DB connections, and secure cache.

    # Register the Blueprint
    app.register_blueprint(routes_bp)

    return app


def get_current_user_id():
    user_id = session.get(USER_ID_KEY)
    return user_id


app = create_app()


def signal_handler(signum, frame):
    logger.info("Received signal %s. Shutting down gracefully...", signum)
    try:
        loop = asyncio.get_event_loop()
        if loop and loop.is_running():
            # Only stop the loop — Quart's shutdown hooks will handle cleanup
            loop.stop()
    except Exception as e:
        logger.error("Error during signal handling: %s", e)
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
            logger.info("Found available port: %s", port)
            return port
        else:
            logger.debug("Port %s is already in use", port)

    logger.error("Could not find an available port after %s attempts", max_attempts)
    sys.exit(1)

if __name__ == "__main__":
    host = os.environ.get('HOST', '127.0.0.1')
    default_port = int(os.environ.get('PORT', 5000))

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    port = find_available_port(default_port, max_attempts=10, host=host)

    try:
        logger.info("Starting NextReel-Lite on http://%s:%s", host, port)
        app.run(
            host=host,
            port=port,
            debug=False,
            use_reloader=False
        )
    except OSError as e:
        import errno
        if e.errno in (errno.EADDRINUSE, 48):  # 48 = macOS, EADDRINUSE = Linux
            logger.error("Port %s is still in use. Please wait a moment and try again.", port)
        else:
            logger.error("Failed to start server: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.error("Application failed to start: %s", e)
        sys.exit(1)
