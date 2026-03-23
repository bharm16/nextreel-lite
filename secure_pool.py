"""Simplified async MySQL pool with SSL, health checks, and slow-query metrics."""

from __future__ import annotations

import asyncio
import ssl
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Dict, Optional

import aiomysql
from aiomysql import Connection, DictCursor, Pool

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
    """Connection pool configuration.

    Some legacy fields are intentionally retained to preserve the existing env
    contract, even though per-user/IP limits are no longer enforced.
    """

    host: str
    user: str
    password: str
    database: str
    port: int = 3306
    min_size: int = 5
    max_size: int = 20
    max_connections_per_user: int = 5
    max_connections_per_ip: int = 10
    connect_timeout: int = 5
    query_timeout: int = 30
    pool_recycle: int = 900
    idle_timeout: int = 300
    max_queries_per_minute: int = 1000
    max_queries_per_user_minute: int = 100
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
        return (datetime.now() - self.created_at).total_seconds()

    def idle_seconds(self) -> float:
        return (datetime.now() - self.last_used_at).total_seconds()


class SecureConnectionPool:
    """Pooled async database access with health and timing metrics."""

    def __init__(self, config: SecurePoolConfig):
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
        self.circuit_breaker_failures = 0
        self.circuit_breaker_last_failure: Optional[datetime] = None
        self.circuit_breaker_state = "closed"
        self._cb_lock = asyncio.Lock()
        self._health_check_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._shutdown = False

    async def init_pool(self):
        """Initialize the underlying aiomysql pool."""
        if self.circuit_breaker_state == "open" and not await self._can_attempt_reconnect():
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
            self._health_check_task = asyncio.create_task(self._health_monitor())
            self._cleanup_task = asyncio.create_task(self._connection_cleanup())
            self.circuit_breaker_state = "closed"
            self.circuit_breaker_failures = 0
            self.state = PoolState.HEALTHY
            logger.info("Secure connection pool initialized successfully")
        except Exception as exc:
            await self._record_circuit_breaker_failure()
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

        if self.circuit_breaker_state == "open" and not await self._can_attempt_reconnect():
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
            self.connections[conn_id] = ConnectionMetadata(
                created_at=datetime.now(),
                last_used_at=datetime.now(),
            )
            self.metrics["active_connections"] += 1

            if self.config.pool_pre_ping:
                try:
                    await connection.ping()
                except Exception as ping_error:
                    connection.close()
                    raise RuntimeError("Connection failed pre-ping check") from ping_error

            if self.circuit_breaker_state == "half-open":
                async with self._cb_lock:
                    self.circuit_breaker_state = "closed"
                    self.circuit_breaker_failures = 0

            yield connection
        except asyncio.TimeoutError as exc:
            self.metrics["connections_failed"] += 1
            await self._record_circuit_breaker_failure()
            raise RuntimeError("Connection acquisition timeout") from exc
        except Exception:
            self.metrics["connections_failed"] += 1
            await self._record_circuit_breaker_failure()
            raise
        finally:
            if connection and conn_id:
                metadata = self.connections.pop(conn_id, None)
                if metadata:
                    metadata.last_used_at = datetime.now()
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
                        "timestamp": datetime.now(),
                    }
                    self.recent_queries.append(query_summary)

                    if query_time > self.config.slow_query_threshold:
                        self.metrics["queries_slow"] += 1
                        self.slow_queries.append(query_summary)
                        logger.warning("Slow query (%.2fs): %s", query_time, query[:50])

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

    async def _health_monitor(self):
        """Monitor pool health and connectivity."""
        while not self._shutdown:
            try:
                await asyncio.sleep(self.config.health_check_interval)

                if not self.pool:
                    self.state = PoolState.FAILED
                    continue

                pool_size = self.pool.size
                free_size = self.pool.freesize
                self.metrics["idle_connections"] = free_size

                usage_percent = (
                    (pool_size - free_size) / pool_size * 100 if pool_size > 0 else 0
                )
                if usage_percent > 90:
                    self.state = PoolState.CRITICAL
                elif usage_percent > 75:
                    self.state = PoolState.DEGRADED
                else:
                    self.state = PoolState.HEALTHY

                try:
                    async with self.acquire() as conn:
                        async with conn.cursor() as cursor:
                            await cursor.execute("SELECT 1")
                            await cursor.fetchone()
                except Exception as exc:
                    self.metrics["health_check_failures"] += 1
                    logger.error("Health check failed: %s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Health monitor error: %s", exc)

    async def _connection_cleanup(self):
        """Recycle aged connections and shrink excess free connections."""
        while not self._shutdown:
            try:
                await asyncio.sleep(30)

                for metadata in list(self.connections.values()):
                    if metadata.age_seconds() > self.config.pool_recycle:
                        self.metrics["connections_recycled"] += 1
                        logger.warning(
                            "Checked-out connection age %.0fs exceeds pool_recycle %ds",
                            metadata.age_seconds(),
                            self.config.pool_recycle,
                        )

                # Active connection shrinking removed — it competed with
                # production traffic for connections.  aiomysql's built-in
                # pool_recycle handles connection lifecycle instead.
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Cleanup error: %s", exc)

    async def _record_circuit_breaker_failure(self):
        async with self._cb_lock:
            self.circuit_breaker_failures += 1
            self.circuit_breaker_last_failure = datetime.now()
            if self.circuit_breaker_failures >= self.config.circuit_breaker_threshold:
                if self.circuit_breaker_state != "open":
                    self.circuit_breaker_state = "open"
                    self.metrics["circuit_breaker_trips"] += 1
                    logger.error("Circuit breaker opened due to repeated failures")

    async def _can_attempt_reconnect(self) -> bool:
        async with self._cb_lock:
            if self.circuit_breaker_state != "open":
                return True
            if self.circuit_breaker_last_failure is None:
                return False
            elapsed = (datetime.now() - self.circuit_breaker_last_failure).total_seconds()
            if elapsed > self.config.circuit_breaker_timeout:
                self.circuit_breaker_state = "half-open"
                logger.info("Circuit breaker entering half-open state")
                return True
            return False

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

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            logger.info("Secure connection pool closed")
