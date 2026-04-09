"""Extracted pool monitoring components — health monitor, cleanup, circuit breaker.

These were previously embedded inside SecureConnectionPool.  Extracting them
into composable collaborators enforces SRP and makes each concern
independently testable.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional

from logging_config import get_logger

if TYPE_CHECKING:
    from infra.pool import PoolState, SecureConnectionPool

logger = get_logger(__name__)


class CircuitBreakerState(str, Enum):
    """Circuit breaker state — inherits str for backward-compat comparisons."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class PoolCircuitBreaker:
    """Circuit breaker for the database connection pool.

    States: closed → open → half-open → closed.
    All mutations are protected by an asyncio.Lock.
    """

    def __init__(self, threshold: int = 5, timeout: int = 30, half_open_requests: int = 3):
        self.threshold = threshold
        self.timeout = timeout
        self.half_open_requests = half_open_requests

        self.state = CircuitBreakerState.CLOSED
        self.failures = 0
        self.last_failure: Optional[datetime] = None
        self.trips = 0
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        """Public accessor for the internal async lock.

        Exposed so callers that need to coordinate with circuit-breaker
        state transitions can ``async with breaker.lock`` without reaching
        into the underscore-prefixed attribute.
        """
        return self._lock

    async def record_failure(self) -> None:
        async with self._lock:
            self.failures += 1
            self.last_failure = datetime.now(timezone.utc)
            if self.failures >= self.threshold:
                if self.state != CircuitBreakerState.OPEN:
                    self.state = CircuitBreakerState.OPEN
                    self.trips += 1
                    logger.error("Pool circuit breaker opened due to repeated failures")

    async def record_success(self) -> None:
        async with self._lock:
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.state = CircuitBreakerState.CLOSED
                self.failures = 0

    async def can_attempt(self) -> bool:
        async with self._lock:
            if self.state == CircuitBreakerState.CLOSED:
                return True
            if self.state == CircuitBreakerState.HALF_OPEN:
                return True
            if self.last_failure is None:
                return False
            elapsed = (datetime.now(timezone.utc) - self.last_failure).total_seconds()
            if elapsed > self.timeout:
                self.state = CircuitBreakerState.HALF_OPEN
                logger.info("Pool circuit breaker entering half-open state")
                return True
            return False

    async def reset(self) -> None:
        async with self._lock:
            self.state = CircuitBreakerState.CLOSED
            self.failures = 0


class _PeriodicTask:
    """Base class for background tasks that run a tick on a fixed interval.

    Subclasses implement ``_tick()``. The base handles start/stop, cancellation,
    per-tick exception logging, and the sleep loop. Existing subclasses
    historically exposed ``_task`` and ``_shutdown`` attributes; those are
    preserved here so any test or caller peeking at them still works.
    """

    def __init__(self, interval: float, name: str):
        self._interval = interval
        self._name = name
        self._task: Optional[asyncio.Task] = None
        self._shutdown = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._shutdown = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._shutdown:
            try:
                await asyncio.sleep(self._interval)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("%s error: %s", self._name, exc)

    async def _tick(self) -> None:
        raise NotImplementedError


class PoolHealthMonitor(_PeriodicTask):
    """Background task that monitors pool health and adjusts pool state."""

    def __init__(self, pool: "SecureConnectionPool", interval: int = 10):
        super().__init__(interval=interval, name="Health monitor")
        self._pool = pool

    async def _tick(self) -> None:
        from infra.pool import PoolState

        pool = self._pool.pool
        if not pool:
            self._pool.state = PoolState.FAILED
            return

        pool_size = pool.size
        free_size = pool.freesize
        self._pool.metrics["idle_connections"] = free_size

        new_state = self._pool.update_state_from_usage(free=free_size, size=pool_size)
        if new_state in (PoolState.CRITICAL, PoolState.DEGRADED):
            # Don't consume a scarce connection for probing
            return

        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    await cursor.fetchone()
        except Exception as exc:
            self._pool.metrics["health_check_failures"] += 1
            logger.error("Health check failed: %s", exc)


class ConnectionCleanup(_PeriodicTask):
    """Background task that warns about aged checked-out connections."""

    def __init__(self, pool: "SecureConnectionPool", interval: int = 30):
        super().__init__(interval=interval, name="Cleanup")
        self._pool = pool

    async def _tick(self) -> None:
        for metadata in list(self._pool.connections.values()):
            if metadata.age_seconds() > self._pool.config.pool_recycle:
                self._pool.metrics["connections_recycled"] += 1
                logger.warning(
                    "Checked-out connection age %.0fs exceeds pool_recycle %ds",
                    metadata.age_seconds(),
                    self._pool.config.pool_recycle,
                )


class SlowQueryLogger:
    """Logs EXPLAIN output for slow SELECT queries (best-effort)."""

    @staticmethod
    async def log_explain(connection, query: str, params) -> None:
        trimmed = query.strip()
        if not trimmed.upper().startswith("SELECT"):
            return
        try:
            async with connection.cursor() as cur:
                await asyncio.wait_for(
                    cur.execute(f"EXPLAIN {trimmed}", params),
                    timeout=5.0,
                )
                rows = await cur.fetchall()
                for row in rows or []:
                    logger.info("EXPLAIN: %s", dict(row))
        except Exception:
            pass  # EXPLAIN is advisory; never block on failure
