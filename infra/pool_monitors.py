"""Extracted pool monitoring components — health monitor, cleanup, circuit breaker.

These were previously embedded inside SecureConnectionPool.  Extracting them
into composable collaborators enforces SRP and makes each concern
independently testable.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from logging_config import get_logger

if TYPE_CHECKING:
    from infra.pool import PoolState, SecureConnectionPool

logger = get_logger(__name__)


class PoolCircuitBreaker:
    """Circuit breaker for the database connection pool.

    States: closed → open → half-open → closed.
    All mutations are protected by an asyncio.Lock.
    """

    def __init__(self, threshold: int = 5, timeout: int = 30, half_open_requests: int = 3):
        self.threshold = threshold
        self.timeout = timeout
        self.half_open_requests = half_open_requests

        self.state = "closed"
        self.failures = 0
        self.last_failure: Optional[datetime] = None
        self.trips = 0
        self._lock = asyncio.Lock()

    async def record_failure(self) -> None:
        async with self._lock:
            self.failures += 1
            self.last_failure = datetime.now(timezone.utc)
            if self.failures >= self.threshold:
                if self.state != "open":
                    self.state = "open"
                    self.trips += 1
                    logger.error("Pool circuit breaker opened due to repeated failures")

    async def record_success(self) -> None:
        async with self._lock:
            if self.state == "half-open":
                self.state = "closed"
                self.failures = 0

    async def can_attempt(self) -> bool:
        async with self._lock:
            if self.state == "closed":
                return True
            if self.state == "half-open":
                return True
            # state == "open"
            if self.last_failure is None:
                return False
            elapsed = (datetime.now(timezone.utc) - self.last_failure).total_seconds()
            if elapsed > self.timeout:
                self.state = "half-open"
                logger.info("Pool circuit breaker entering half-open state")
                return True
            return False

    async def reset(self) -> None:
        async with self._lock:
            self.state = "closed"
            self.failures = 0


class PoolHealthMonitor:
    """Background task that monitors pool health and adjusts pool state."""

    def __init__(self, pool: "SecureConnectionPool", interval: int = 10):
        self._pool = pool
        self._interval = interval
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
        from infra.pool import PoolState

        while not self._shutdown:
            try:
                await asyncio.sleep(self._interval)

                pool = self._pool.pool
                if not pool:
                    self._pool.state = PoolState.FAILED
                    continue

                pool_size = pool.size
                free_size = pool.freesize
                self._pool.metrics["idle_connections"] = free_size

                usage_percent = (
                    (pool_size - free_size) / pool_size * 100 if pool_size > 0 else 0
                )
                if usage_percent > 90:
                    self._pool.state = PoolState.CRITICAL
                    continue  # Don't consume a scarce connection for probing
                elif usage_percent > 75:
                    self._pool.state = PoolState.DEGRADED
                    continue
                else:
                    self._pool.state = PoolState.HEALTHY

                try:
                    async with self._pool.acquire() as conn:
                        async with conn.cursor() as cursor:
                            await cursor.execute("SELECT 1")
                            await cursor.fetchone()
                except Exception as exc:
                    self._pool.metrics["health_check_failures"] += 1
                    logger.error("Health check failed: %s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Health monitor error: %s", exc)


class ConnectionCleanup:
    """Background task that warns about aged checked-out connections."""

    def __init__(self, pool: "SecureConnectionPool", interval: int = 30):
        self._pool = pool
        self._interval = interval
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

                for metadata in list(self._pool.connections.values()):
                    if metadata.age_seconds() > self._pool.config.pool_recycle:
                        self._pool.metrics["connections_recycled"] += 1
                        logger.warning(
                            "Checked-out connection age %.0fs exceeds pool_recycle %ds",
                            metadata.age_seconds(),
                            self._pool.config.pool_recycle,
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Cleanup error: %s", exc)


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
