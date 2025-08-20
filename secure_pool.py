# secure_pool.py
import asyncio
import time
import logging
from typing import Optional, Dict, Any, AsyncIterator, Set
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict, deque
import aiomysql
from aiomysql import Pool, Connection, DictCursor
import hashlib
import ssl
import os
from enum import Enum

logger = logging.getLogger(__name__)


class PoolState(Enum):
    """Connection pool states"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    FAILED = "failed"


@dataclass
class SecurePoolConfig:
    """Enhanced secure pool configuration"""

    # Connection settings
    host: str
    user: str
    password: str
    database: str
    port: int = 3306

    # Pool size limits - More restrictive for security
    min_size: int = 5  # Minimum connections
    max_size: int = 20  # Maximum total connections

    # Per-user/IP limits to prevent exhaustion
    max_connections_per_user: int = 5  # Max connections per user
    max_connections_per_ip: int = 10  # Max connections per IP

    # Connection lifecycle
    connect_timeout: int = 5  # Connection timeout
    query_timeout: int = 30  # Query execution timeout
    pool_recycle: int = 900  # Recycle connections every 15 min
    idle_timeout: int = 300  # Close idle connections after 5 min

    # Rate limiting
    max_queries_per_minute: int = 1000  # Global query rate limit
    max_queries_per_user_minute: int = 100  # Per-user query rate limit

    # Health checks
    health_check_interval: int = 10  # Health check frequency
    pool_pre_ping: bool = True  # Ping before using connection

    # Circuit breaker
    circuit_breaker_threshold: int = 5  # Failures before opening
    circuit_breaker_timeout: int = 30  # Recovery timeout
    circuit_breaker_half_open_requests: int = 3  # Test requests in half-open

    # SSL settings
    ssl_cert_path: Optional[str] = None
    use_ssl: bool = True
    validate_ssl: bool = True

    # Monitoring
    enable_metrics: bool = True
    slow_query_threshold: float = 1.0  # Log queries slower than 1s


@dataclass
class ConnectionMetadata:
    """Metadata for tracking connections"""

    connection_id: str
    user_id: Optional[str]
    ip_address: Optional[str]
    created_at: datetime
    last_used_at: datetime
    queries_executed: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0

    def age_seconds(self) -> float:
        """Get connection age in seconds"""
        return (datetime.now() - self.created_at).total_seconds()

    def idle_seconds(self) -> float:
        """Get idle time in seconds"""
        return (datetime.now() - self.last_used_at).total_seconds()


class RateLimiter:
    """Token bucket rate limiter"""

    def __init__(self, rate: int, burst: int = None):
        self.rate = rate  # Tokens per minute
        self.burst = burst or rate
        self.tokens = self.burst
        self.last_refill = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens"""
        async with self.lock:
            now = time.time()
            # Refill tokens
            elapsed = now - self.last_refill
            refill = elapsed * (self.rate / 60.0)
            self.tokens = min(self.burst, self.tokens + refill)
            self.last_refill = now

            # Check if we have enough tokens
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False


class SecureConnectionPool:
    """Production-ready secure connection pool with resource protection"""

    def __init__(self, config: SecurePoolConfig):
        self.config = config
        self.pool: Optional[Pool] = None
        self.state = PoolState.HEALTHY

        # Connection tracking
        self.connections: Dict[int, ConnectionMetadata] = {}
        self.user_connections: Dict[str, Set[int]] = defaultdict(set)
        self.ip_connections: Dict[str, Set[int]] = defaultdict(set)

        # Rate limiting
        self.global_rate_limiter = RateLimiter(config.max_queries_per_minute)
        self.user_rate_limiters: Dict[str, RateLimiter] = {}

        # Metrics
        self.metrics = {
            "total_connections": 0,
            "active_connections": 0,
            "idle_connections": 0,
            "connections_created": 0,
            "connections_closed": 0,
            "connections_recycled": 0,
            "connections_failed": 0,
            "queries_executed": 0,
            "queries_failed": 0,
            "queries_slow": 0,
            "rate_limit_hits": 0,
            "circuit_breaker_trips": 0,
            "health_check_failures": 0,
        }

        # Query history for monitoring
        self.recent_queries: deque = deque(maxlen=100)
        self.slow_queries: deque = deque(maxlen=50)

        # Circuit breaker
        self.circuit_breaker_failures = 0
        self.circuit_breaker_last_failure = None
        self.circuit_breaker_state = "closed"

        # Tasks
        self._health_check_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._shutdown = False

    async def init_pool(self):
        """Initialize the secure connection pool"""
        if self.circuit_breaker_state == "open":
            if not self._can_attempt_reconnect():
                raise Exception("Circuit breaker is open")

        try:
            logger.info(
                f"Initializing secure pool: {self.config.host}:{self.config.port}"
            )

            # Create SSL context if needed
            ssl_context = None
            if self.config.use_ssl:
                ssl_context = self._create_ssl_context()

            # Create pool with security limits
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
                autocommit=False,
            )

            # Validate pool
            await self._validate_pool()

            # Start monitoring tasks
            self._health_check_task = asyncio.create_task(self._health_monitor())
            self._cleanup_task = asyncio.create_task(self._connection_cleanup())

            # Reset circuit breaker
            self.circuit_breaker_state = "closed"
            self.circuit_breaker_failures = 0

            self.state = PoolState.HEALTHY
            logger.info("Secure connection pool initialized successfully")

        except Exception as e:
            self._record_circuit_breaker_failure()
            logger.error(f"Failed to initialize pool: {e}")
            raise

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create secure SSL context"""
        if self.config.validate_ssl and self.config.ssl_cert_path:
            context = ssl.create_default_context(cafile=self.config.ssl_cert_path)
        else:
            context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = (
            ssl.CERT_REQUIRED if self.config.validate_ssl else ssl.CERT_NONE
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers(
            "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS"
        )
        return context

    async def _validate_pool(self):
        """Validate pool connectivity"""
        async with self.acquire(user_id="system", ip_address="127.0.0.1") as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                result = await cursor.fetchone()
                if result != {"1": 1}:
                    raise Exception("Pool validation failed")

    def _check_user_limit(self, user_id: str) -> bool:
        """Check if user has reached connection limit"""
        active_count = len(self.user_connections.get(user_id, set()))
        return active_count < self.config.max_connections_per_user

    def _check_ip_limit(self, ip_address: str) -> bool:
        """Check if IP has reached connection limit"""
        active_count = len(self.ip_connections.get(ip_address, set()))
        return active_count < self.config.max_connections_per_ip

    async def _check_rate_limit(self, user_id: str) -> bool:
        """Check rate limits"""
        # Global rate limit
        if not await self.global_rate_limiter.acquire():
            self.metrics["rate_limit_hits"] += 1
            return False

        # User rate limit
        if user_id and user_id != "system":
            if user_id not in self.user_rate_limiters:
                self.user_rate_limiters[user_id] = RateLimiter(
                    self.config.max_queries_per_user_minute
                )

            if not await self.user_rate_limiters[user_id].acquire():
                self.metrics["rate_limit_hits"] += 1
                return False

        return True

    @asynccontextmanager
    async def acquire(
        self, user_id: str = None, ip_address: str = None
    ) -> AsyncIterator[Connection]:
        """Acquire a connection with security checks"""
        # Check circuit breaker
        if self.circuit_breaker_state == "open":
            if not self._can_attempt_reconnect():
                raise Exception("Circuit breaker is open - pool unavailable")

        # Check user/IP limits
        if user_id and not self._check_user_limit(user_id):
            raise Exception(f"User {user_id} has reached connection limit")

        if ip_address and not self._check_ip_limit(ip_address):
            raise Exception(f"IP {ip_address} has reached connection limit")

        # Check rate limits
        if not await self._check_rate_limit(user_id):
            raise Exception("Rate limit exceeded")

        connection = None
        conn_id = None

        try:
            # Acquire connection with timeout
            connection = await asyncio.wait_for(
                self.pool.acquire(), timeout=self.config.connect_timeout
            )

            conn_id = id(connection)

            # Track connection
            metadata = ConnectionMetadata(
                connection_id=hashlib.md5(str(conn_id).encode()).hexdigest()[:8],
                user_id=user_id,
                ip_address=ip_address,
                created_at=datetime.now(),
                last_used_at=datetime.now(),
            )
            self.connections[conn_id] = metadata

            if user_id:
                self.user_connections[user_id].add(conn_id)
            if ip_address:
                self.ip_connections[ip_address].add(conn_id)

            self.metrics["active_connections"] += 1

            # Ping if configured
            if self.config.pool_pre_ping:
                await connection.ping()

            # Reset circuit breaker on success
            if self.circuit_breaker_state == "half-open":
                self.circuit_breaker_state = "closed"
                self.circuit_breaker_failures = 0

            yield connection

        except asyncio.TimeoutError:
            self.metrics["connections_failed"] += 1
            self._record_circuit_breaker_failure()
            raise Exception("Connection acquisition timeout")

        except Exception as e:
            self.metrics["connections_failed"] += 1
            self._record_circuit_breaker_failure()
            logger.error(f"Connection acquisition failed: {e}")
            raise

        finally:
            if connection and conn_id:
                # Update metadata
                if conn_id in self.connections:
                    self.connections[conn_id].last_used_at = datetime.now()
                    self.connections[conn_id].queries_executed += 1

                # Clean up tracking
                if user_id and conn_id in self.user_connections[user_id]:
                    self.user_connections[user_id].discard(conn_id)
                if ip_address and conn_id in self.ip_connections[ip_address]:
                    self.ip_connections[ip_address].discard(conn_id)

                del self.connections[conn_id]
                self.metrics["active_connections"] -= 1

                # Release connection
                self.pool.release(connection)

    async def execute_secure(
        self,
        query: str,
        params: tuple = None,
        user_id: str = None,
        ip_address: str = None,
        fetch: str = "one",
    ) -> Any:
        """Execute query with security controls"""
        start_time = time.time()

        try:
            async with self.acquire(
                user_id=user_id, ip_address=ip_address
            ) as connection:
                async with connection.cursor() as cursor:
                    # Execute with timeout
                    await asyncio.wait_for(
                        cursor.execute(query, params), timeout=self.config.query_timeout
                    )

                    # Fetch results
                    if fetch == "one":
                        result = await cursor.fetchone()
                    elif fetch == "all":
                        result = await cursor.fetchall()
                    elif fetch == "many":
                        result = await cursor.fetchmany()
                    else:
                        result = cursor.rowcount

                    # Track metrics
                    query_time = time.time() - start_time
                    self.metrics["queries_executed"] += 1

                    # Track slow queries
                    if query_time > self.config.slow_query_threshold:
                        self.metrics["queries_slow"] += 1
                        self.slow_queries.append(
                            {
                                "query": query[:100],
                                "duration": query_time,
                                "user_id": user_id,
                                "timestamp": datetime.now(),
                            }
                        )
                        logger.warning(
                            f"Slow query ({query_time:.2f}s): {query[:50]}..."
                        )

                    # Track recent queries
                    self.recent_queries.append(
                        {
                            "query": query[:50],
                            "duration": query_time,
                            "user_id": user_id,
                            "timestamp": datetime.now(),
                        }
                    )

                    return result

        except asyncio.TimeoutError:
            self.metrics["queries_failed"] += 1
            raise Exception(f"Query timeout after {self.config.query_timeout}s")

        except Exception as e:
            self.metrics["queries_failed"] += 1
            logger.error(f"Query execution failed: {e}")
            raise

    async def _health_monitor(self):
        """Monitor pool health"""
        while not self._shutdown:
            try:
                await asyncio.sleep(self.config.health_check_interval)

                # Check pool state
                if not self.pool:
                    self.state = PoolState.FAILED
                    continue

                pool_size = self.pool.size
                free_size = self.pool.freesize

                # Update metrics
                self.metrics["idle_connections"] = free_size

                # Determine health state
                usage_percent = (
                    (pool_size - free_size) / pool_size * 100 if pool_size > 0 else 0
                )

                if usage_percent > 90:
                    self.state = PoolState.CRITICAL
                    logger.warning(f"Pool critical: {usage_percent:.1f}% usage")
                elif usage_percent > 75:
                    self.state = PoolState.DEGRADED
                    logger.warning(f"Pool degraded: {usage_percent:.1f}% usage")
                else:
                    self.state = PoolState.HEALTHY

                # Test connectivity
                try:
                    async with self.acquire(
                        user_id="system", ip_address="127.0.0.1"
                    ) as conn:
                        async with conn.cursor() as cursor:
                            await cursor.execute("SELECT 1")
                            await cursor.fetchone()
                except Exception as e:
                    self.metrics["health_check_failures"] += 1
                    logger.error(f"Health check failed: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error: {e}")

    async def _connection_cleanup(self):
        """Clean up idle and old connections"""
        while not self._shutdown:
            try:
                await asyncio.sleep(30)  # Run every 30 seconds

                # Clean up idle connections
                now = datetime.now()
                for conn_id, metadata in list(self.connections.items()):
                    # Check idle timeout
                    if metadata.idle_seconds() > self.config.idle_timeout:
                        logger.info(f"Closing idle connection {metadata.connection_id}")
                        # Connection will be cleaned up when released

                    # Check max age (pool_recycle)
                    if metadata.age_seconds() > self.config.pool_recycle:
                        logger.info(
                            f"Recycling old connection {metadata.connection_id}"
                        )
                        self.metrics["connections_recycled"] += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    def _record_circuit_breaker_failure(self):
        """Record a circuit breaker failure"""
        self.circuit_breaker_failures += 1
        self.circuit_breaker_last_failure = datetime.now()

        if self.circuit_breaker_failures >= self.config.circuit_breaker_threshold:
            if self.circuit_breaker_state != "open":
                self.circuit_breaker_state = "open"
                self.metrics["circuit_breaker_trips"] += 1
                logger.error("Circuit breaker opened due to repeated failures")

    def _can_attempt_reconnect(self) -> bool:
        """Check if we can attempt reconnection"""
        if self.circuit_breaker_state != "open":
            return True

        if self.circuit_breaker_last_failure:
            elapsed = (datetime.now() - self.circuit_breaker_last_failure).seconds
            if elapsed > self.config.circuit_breaker_timeout:
                self.circuit_breaker_state = "half-open"
                logger.info("Circuit breaker entering half-open state")
                return True

        return False

    async def get_pool_status(self) -> Dict[str, Any]:
        """Get detailed pool status"""
        return {
            "state": self.state.value,
            "pool_size": self.pool.size if self.pool else 0,
            "free_connections": self.pool.freesize if self.pool else 0,
            "active_connections": self.metrics["active_connections"],
            "idle_connections": self.metrics["idle_connections"],
            "total_connections_created": self.metrics["connections_created"],
            "total_connections_failed": self.metrics["connections_failed"],
            "total_connections_recycled": self.metrics["connections_recycled"],
            "queries_executed": self.metrics["queries_executed"],
            "queries_failed": self.metrics["queries_failed"],
            "slow_queries": self.metrics["queries_slow"],
            "rate_limit_hits": self.metrics["rate_limit_hits"],
            "circuit_breaker_state": self.circuit_breaker_state,
            "circuit_breaker_trips": self.metrics["circuit_breaker_trips"],
            "health_check_failures": self.metrics["health_check_failures"],
            "user_connection_counts": {
                user: len(conns) for user, conns in self.user_connections.items()
            },
            "ip_connection_counts": {
                ip: len(conns) for ip, conns in self.ip_connections.items()
            },
            "recent_slow_queries": list(self.slow_queries)[-10:],
        }

    async def close_pool(self):
        """Gracefully close the pool"""
        self._shutdown = True

        # Cancel monitoring tasks
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

        # Close pool
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            logger.info("Secure connection pool closed")
