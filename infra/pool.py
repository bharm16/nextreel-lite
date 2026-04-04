"""Async MySQL connection pool with SSL, health checks, and slow-query metrics.

Contains both the low-level ``SecureConnectionPool`` and the higher-level
``DatabaseConnectionPool`` wrapper with global pool management helpers.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import ssl
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Dict, Optional

import aiomysql
from aiomysql import Connection, DictCursor, Pool

from infra.errors import DatabaseError
from logging_config import get_logger

logger = get_logger(__name__)


class PoolState(Enum):
    """Connection pool health states."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    FAILED = "failed"


@dataclass
class SecurePoolConfig:
    """Connection pool configuration."""

    host: str
    user: str
    password: str
    database: str
    port: int = 3306
    min_size: int = 5
    max_size: int = 20
    connect_timeout: int = 5
    query_timeout: int = 30
    pool_recycle: int = 900
    idle_timeout: int = 300
    health_check_interval: int = 10
    pool_pre_ping: bool = False  # Rely on pool_recycle; avoids extra RTT per query
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: int = 30
    circuit_breaker_half_open_requests: int = 3
    ssl_cert_path: Optional[str] = None
    use_ssl: bool = True
    validate_ssl: bool = True
    enable_metrics: bool = True
    slow_query_threshold: float = 1.0


@dataclass
class ConnectionMetadata:
    """Minimal metadata for checked-out connections."""

    created_at: datetime
    last_used_at: datetime
    query_count: int = 0

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()

    def idle_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.last_used_at).total_seconds()


class SecureConnectionPool:
    """Pooled async database access with health and timing metrics."""

    def __init__(self, config: SecurePoolConfig):
        from infra.pool_monitors import (
            ConnectionCleanup,
            PoolCircuitBreaker,
            PoolHealthMonitor,
            SlowQueryLogger,
        )

        self.config = config
        self.pool: Optional[Pool] = None
        self.state = PoolState.HEALTHY
        self.connections: Dict[int, ConnectionMetadata] = {}
        self.metrics = {
            "active_connections": 0,
            "idle_connections": 0,
            "connections_failed": 0,
            "connections_recycled": 0,
            "queries_executed": 0,
            "queries_failed": 0,
            "queries_slow": 0,
            "rate_limit_hits": 0,
            "circuit_breaker_trips": 0,
            "health_check_failures": 0,
        }
        self.recent_queries: deque = deque(maxlen=100)
        self.slow_queries: deque = deque(maxlen=50)
        self._shutdown = False

        # Composed collaborators (extracted for SRP)
        self._circuit_breaker = PoolCircuitBreaker(
            threshold=config.circuit_breaker_threshold,
            timeout=config.circuit_breaker_timeout,
            half_open_requests=config.circuit_breaker_half_open_requests,
        )
        self._health_monitor = PoolHealthMonitor(self, interval=config.health_check_interval)
        self._connection_cleanup = ConnectionCleanup(self)
        self._slow_query_logger = SlowQueryLogger()

    # Backward-compatible properties for code that reads circuit breaker state directly
    @property
    def circuit_breaker_state(self) -> str:
        return self._circuit_breaker.state

    @circuit_breaker_state.setter
    def circuit_breaker_state(self, value: str) -> None:
        self._circuit_breaker.state = value

    @property
    def circuit_breaker_failures(self) -> int:
        return self._circuit_breaker.failures

    @circuit_breaker_failures.setter
    def circuit_breaker_failures(self, value: int) -> None:
        self._circuit_breaker.failures = value

    @property
    def _cb_lock(self) -> asyncio.Lock:
        return self._circuit_breaker._lock

    async def init_pool(self):
        """Initialize the underlying aiomysql pool."""
        if not await self._circuit_breaker.can_attempt():
            raise RuntimeError("Circuit breaker is open")

        try:
            logger.info(
                "Initializing secure pool: %s:%s",
                self.config.host,
                self.config.port,
            )
            ssl_context = self._create_ssl_context() if self.config.use_ssl else None
            self.pool = await aiomysql.create_pool(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                db=self.config.database,
                minsize=self.config.min_size,
                maxsize=self.config.max_size,
                connect_timeout=self.config.connect_timeout,
                ssl=ssl_context,
                cursorclass=DictCursor,
                pool_recycle=self.config.pool_recycle,
                echo=False,
                autocommit=True,
            )
            await self._validate_pool()
            self._health_monitor.start()
            self._connection_cleanup.start()
            await self._circuit_breaker.reset()
            self.state = PoolState.HEALTHY
            logger.info("Secure connection pool initialized successfully")
        except Exception as exc:
            await self._circuit_breaker.record_failure()
            self.metrics["circuit_breaker_trips"] = self._circuit_breaker.trips
            logger.error("Failed to initialize pool: %s", exc)
            raise

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create the SSL context for database connections."""
        if self.config.validate_ssl and self.config.ssl_cert_path:
            context = ssl.create_default_context(cafile=self.config.ssl_cert_path)
        else:
            context = ssl.create_default_context()
        # MySQL servers typically use IP-based certificates so hostname
        # verification is disabled, but we always require certificate
        # validation to prevent MITM attacks.  CERT_NONE is never used.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers(
            "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS"
        )
        return context

    async def _validate_pool(self):
        """Validate connectivity."""
        async with self.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                result = await cursor.fetchone()
                if result != {"1": 1}:
                    raise RuntimeError("Pool validation failed")

    @asynccontextmanager
    async def acquire(
        self, user_id: str | None = None, ip_address: str | None = None
    ) -> AsyncIterator[Connection]:
        """Acquire a connection.

        ``user_id`` and ``ip_address`` are accepted for backward compatibility
        but are no longer used for per-user/IP accounting.
        """
        del user_id, ip_address

        if not await self._circuit_breaker.can_attempt():
            raise RuntimeError("Circuit breaker is open - pool unavailable")

        if not self.pool:
            raise RuntimeError("Connection pool is not initialized")

        connection = None
        conn_id = None

        try:
            connection = await asyncio.wait_for(
                self.pool.acquire(), timeout=self.config.connect_timeout
            )
            conn_id = id(connection)
            now = datetime.now(timezone.utc)
            self.connections[conn_id] = ConnectionMetadata(
                created_at=now,
                last_used_at=now,
            )
            self.metrics["active_connections"] += 1

            if self.config.pool_pre_ping:
                try:
                    await connection.ping()
                except Exception as ping_error:
                    connection.close()
                    raise RuntimeError("Connection failed pre-ping check") from ping_error

            await self._circuit_breaker.record_success()

            yield connection
        except asyncio.TimeoutError as exc:
            self.metrics["connections_failed"] += 1
            await self._circuit_breaker.record_failure()
            self.metrics["circuit_breaker_trips"] = self._circuit_breaker.trips
            raise RuntimeError("Connection acquisition timeout") from exc
        except Exception:
            self.metrics["connections_failed"] += 1
            await self._circuit_breaker.record_failure()
            self.metrics["circuit_breaker_trips"] = self._circuit_breaker.trips
            raise
        finally:
            if connection and conn_id:
                metadata = self.connections.pop(conn_id, None)
                if metadata:
                    metadata.last_used_at = datetime.now(timezone.utc)
                    metadata.query_count += 1
                self.metrics["active_connections"] = max(
                    0, self.metrics["active_connections"] - 1
                )
                self.pool.release(connection)

    async def execute_secure(
        self,
        query: str,
        params: tuple | list | None = None,
        user_id: str | None = None,
        ip_address: str | None = None,
        fetch: str = "one",
    ) -> Any:
        """Execute a query with timeout, metrics, and slow-query tracking."""
        del user_id, ip_address
        start_time = time.time()

        try:
            async with self.acquire() as connection:
                async with connection.cursor() as cursor:
                    await asyncio.wait_for(
                        cursor.execute(query, params), timeout=self.config.query_timeout
                    )

                    if fetch == "one":
                        result = await cursor.fetchone()
                    elif fetch == "all":
                        result = await cursor.fetchall()
                    elif fetch == "many":
                        result = await cursor.fetchmany()
                    else:
                        result = cursor.rowcount

                    query_time = time.time() - start_time
                    self.metrics["queries_executed"] += 1

                    query_summary = {
                        "query": query[:100],
                        "duration": query_time,
                        "timestamp": datetime.now(timezone.utc),
                    }
                    self.recent_queries.append(query_summary)

                    if query_time > self.config.slow_query_threshold:
                        self.metrics["queries_slow"] += 1
                        self.slow_queries.append(query_summary)
                        logger.warning("Slow query (%.2fs): %s", query_time, query[:50])
                        await self._slow_query_logger.log_explain(connection, query, params)

                    return result
        except asyncio.TimeoutError as exc:
            self.metrics["queries_failed"] += 1
            raise RuntimeError(
                f"Query timeout after {self.config.query_timeout}s"
            ) from exc
        except Exception as exc:
            self.metrics["queries_failed"] += 1
            logger.error("Query execution failed: %s", exc)
            raise

    async def get_pool_status(self) -> Dict[str, Any]:
        """Return pool health and metrics."""
        return {
            "state": self.state.value,
            "pool_size": self.pool.size if self.pool else 0,
            "free_connections": self.pool.freesize if self.pool else 0,
            "active_connections": self.metrics["active_connections"],
            "idle_connections": self.metrics["idle_connections"],
            "total_connections_failed": self.metrics["connections_failed"],
            "total_connections_recycled": self.metrics["connections_recycled"],
            "queries_executed": self.metrics["queries_executed"],
            "queries_failed": self.metrics["queries_failed"],
            "slow_queries": self.metrics["queries_slow"],
            "rate_limit_hits": self.metrics["rate_limit_hits"],
            "circuit_breaker_state": self.circuit_breaker_state,
            "circuit_breaker_trips": self.metrics["circuit_breaker_trips"],
            "health_check_failures": self.metrics["health_check_failures"],
            "user_connection_counts": {},
            "ip_connection_counts": {},
            # Strip raw query text to avoid leaking parameter values.
            "recent_slow_queries": [
                {"duration": q["duration"], "timestamp": q["timestamp"]}
                for q in list(self.slow_queries)[-10:]
            ],
        }

    async def close_pool(self):
        """Gracefully close monitoring tasks and the underlying pool."""
        self._shutdown = True

        await self._health_monitor.stop()
        await self._connection_cleanup.stop()

        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            logger.info("Secure connection pool closed")


# ---------------------------------------------------------------------------
# DatabaseConnectionPool — backward-compatible wrapper
# ---------------------------------------------------------------------------


class DatabaseConnectionPool:
    """Wrapper for backward compatibility with secure pool"""

    def __init__(self, db_config: dict) -> None:
        from config.database import DatabaseConfig

        from config.env import get_environment

        flask_env = get_environment()

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
            min_size=int(os.getenv("POOL_MIN_SIZE", DatabaseConfig.POOL_MIN_SIZE)),
            max_size=int(os.getenv("POOL_MAX_SIZE", DatabaseConfig.POOL_MAX_SIZE)),
            connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", 5)),
            query_timeout=int(os.getenv("DB_QUERY_TIMEOUT", 30)),
            pool_recycle=int(os.getenv("DB_POOL_RECYCLE", 900)),
            idle_timeout=int(os.getenv("DB_IDLE_TIMEOUT", 300)),
            ssl_cert_path=ssl_cert,
            use_ssl=DatabaseConfig.use_ssl(),
            validate_ssl=validate_ssl,
            slow_query_threshold=float(os.getenv("SLOW_QUERY_THRESHOLD", 1.0)),
        )

        self.pool = SecureConnectionPool(self.secure_config)

    async def init_pool(self) -> None:
        """Initialize the pool"""
        await self.pool.init_pool()

    def acquire(self, user_id: str | None = None, ip_address: str | None = None):
        """Acquire a connection — returns an async context manager."""
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

    async def get_async_connection(self):
        """Removed — the returned connection was released before the caller used it.

        Use ``async with pool.acquire() as conn:`` instead.
        """
        raise NotImplementedError(
            "get_async_connection is removed. Use 'async with pool.acquire() as conn:' instead."
        )

    async def release_async_connection(self, conn):
        """No-op — connections are auto-released by the context manager."""

    def __repr__(self) -> str:
        return (
            f"<SecureConnectionPool "
            f"host={self.secure_config.host} "
            f"database={self.secure_config.database}>"
        )


# ---------------------------------------------------------------------------
# Global pool instance management
# ---------------------------------------------------------------------------

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
