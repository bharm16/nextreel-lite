"""
Database connection pool module.

Provides ``DatabaseConnectionPool`` as a backward-compatible wrapper around
``SecureConnectionPool`` and global pool management helpers.
"""

import asyncio
import atexit
import os

from database.errors import DatabaseError
from secure_pool import SecureConnectionPool, SecurePoolConfig
from logging_config import get_logger

logger = get_logger(__name__)


class DatabaseConnectionPool:
    """Wrapper for backward compatibility with secure pool"""

    def __init__(self, db_config: dict) -> None:
        from config.database import DatabaseConfig

        flask_env = os.getenv("FLASK_ENV", "development")

        # Convert to secure pool config
        # Default to True in production to enforce SSL certificate validation.
        # Set VALIDATE_SSL=false explicitly in dev/test environments.
        validate_ssl = os.getenv("VALIDATE_SSL", "true" if flask_env == "production" else "false").lower() == "true"
        ssl_cert = (
            DatabaseConfig.get_ssl_cert_path()
            if validate_ssl
            else None
        )
        self.secure_config = SecurePoolConfig(
            host=db_config["host"],
            port=db_config.get("port", 3306),
            user=db_config["user"],
            password=db_config["password"],
            database=db_config["database"],
            min_size=int(os.getenv("POOL_MIN_SIZE", 5)),
            max_size=int(os.getenv("POOL_MAX_SIZE", 20)),
            max_connections_per_user=int(os.getenv("MAX_CONN_PER_USER", 10)),
            max_connections_per_ip=int(os.getenv("MAX_CONN_PER_IP", 20)),
            connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", 5)),
            query_timeout=int(os.getenv("DB_QUERY_TIMEOUT", 30)),
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE", 900)),
            idle_timeout=int(os.getenv("DB_IDLE_TIMEOUT", 300)),
            max_queries_per_minute=int(os.getenv("MAX_QUERIES_PER_MIN", 5000)),
            max_queries_per_user_minute=int(os.getenv("MAX_USER_QUERIES_PER_MIN", 500)),
            ssl_cert_path=ssl_cert,
            use_ssl=DatabaseConfig.use_ssl(),
            validate_ssl=validate_ssl,
            slow_query_threshold=float(os.getenv("SLOW_QUERY_THRESHOLD", 1.0)),
        )

        self.pool = SecureConnectionPool(self.secure_config)

    async def init_pool(self) -> None:
        """Initialize the pool"""
        await self.pool.init_pool()

    async def acquire(self, user_id: str | None = None, ip_address: str | None = None):
        """Acquire a connection"""
        return self.pool.acquire(user_id=user_id, ip_address=ip_address)

    async def execute(self, query: str, params: list | tuple | None = None, fetch: str = "one", user_id: str | None = None):
        """Execute a query"""
        try:
            return await self.pool.execute_secure(
                query, params, user_id=user_id, fetch=fetch
            )
        except DatabaseError:
            raise
        except Exception as exc:
            logger.error("Database query failed: %s", exc, exc_info=True)
            raise DatabaseError(f"Query failed: {exc}") from exc

    async def close_pool(self) -> None:
        """Close the pool"""
        await self.pool.close_pool()

    async def get_metrics(self) -> dict:
        """Get pool metrics"""
        return await self.pool.get_pool_status()

    # Legacy methods for backward compatibility
    async def get_async_connection(self):
        """Legacy method - prefer using acquire() context manager"""
        async with self.pool.acquire() as conn:
            return conn

    async def release_async_connection(self, conn):
        """Legacy method - connection is auto-released with context manager"""
        pass

    def __repr__(self) -> str:
        return (
            f"<SecureConnectionPool "
            f"host={self.secure_config.host} "
            f"database={self.secure_config.database}>"
        )


# Global pool instance management
_pool = None
_pool_lock = asyncio.Lock()


async def init_pool():
    """Initialize the global database connection pool."""
    global _pool
    async with _pool_lock:
        if _pool is None:
            from config.database import DatabaseConfig

            db_config = DatabaseConfig.get_db_config()
            _pool = DatabaseConnectionPool(db_config)
            await _pool.init_pool()
            logger.info("Global database pool initialized")
    return _pool


async def get_pool():
    """Get the global database connection pool, initializing if needed."""
    if _pool is None:
        await init_pool()
    return _pool


async def close_pool():
    """Close the global database connection pool gracefully."""
    global _pool
    if _pool:
        try:
            await _pool.close_pool()
            _pool = None
            logger.info("Global database pool closed successfully")
        except Exception as e:
            logger.error("Error closing global database pool: %s", e)
            _pool = None


# Register cleanup at exit
def _cleanup_pool_sync():
    """Synchronous cleanup for atexit."""
    global _pool
    if _pool:
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and not loop.is_closed():
                loop.run_until_complete(close_pool())
            else:
                # No running loop — create a new one for cleanup
                asyncio.run(close_pool())
        except Exception as e:
            logger.warning("Could not cleanly close pool at exit: %s", e)


atexit.register(_cleanup_pool_sync)
