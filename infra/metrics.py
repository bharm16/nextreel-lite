"""
Prometheus metrics collection for Nextreel application.
Provides detailed metrics that integrate with Grafana Cloud.

Metrics are organized into domain groups in ``infra.metrics_groups``.
This module re-exports the individual metric objects for backward
compatibility so that existing ``from infra.metrics import X`` imports
continue to work.
"""

import time
import asyncio
from typing import Optional
from prometheus_client import generate_latest, REGISTRY
from quart import Response, request, g
from infra.cache import LruExpiringMap
from logging_config import get_logger

_CIRCUIT_STATE_METRIC_VALUES = {"closed": 0, "open": 1, "half-open": 2}

# Grouped metrics — the canonical source for all Prometheus objects.
from infra.metrics_groups import (
    app_info,
    http,
    database,
    movie,
    tmdb,
    user,
    cache,
    error,
    enrichment,
    worker as worker_metrics_group,
    logging_metrics as logging_metrics_group,
)

logger = get_logger(__name__)

# ── Backward-compatible aliases ──────────────────────────────────────
# Existing code imports these names directly; keep them working.
http_requests_total = http.requests_total
http_request_duration_seconds = http.request_duration_seconds
http_requests_in_progress = http.requests_in_progress

db_connections_active = database.connections_active
db_connections_idle = database.connections_idle
db_connections_total = database.connections_total
db_queries_total = database.queries_total
db_query_duration_seconds = database.query_duration_seconds
db_circuit_breaker_state = database.circuit_breaker_state
db_connection_errors_total = database.connection_errors_total

movie_recommendations_total = movie.recommendations_total
movie_fetches_total = movie.fetches_total
movie_filters_applied_total = movie.filters_applied_total

tmdb_api_calls_total = tmdb.api_calls_total
tmdb_api_duration_seconds = tmdb.api_duration_seconds
tmdb_rate_limit_remaining = tmdb.rate_limit_remaining

active_users = user.active_users
user_sessions_total = user.sessions_total
user_actions_total = user.actions_total
session_duration_seconds = user.session_duration_seconds

cache_hits_total = cache.hits_total
cache_misses_total = cache.misses_total
cache_operations_duration_seconds = cache.operations_duration_seconds
rate_limit_backend_mode = cache.rate_limit_backend_mode

application_errors_total = error.application_errors_total
navigation_state_redis_import_total = error.navigation_state_redis_import_total
navigation_state_migration_miss_total = error.navigation_state_migration_miss_total
navigation_state_conflicts_total = error.navigation_state_conflicts_total
home_prewarm_failed_total = error.home_prewarm_failed_total

enrichment_enqueued_total = enrichment.enqueued_total
enrichment_enqueue_fallback_total = enrichment.enqueue_fallback_total
enrichment_backlog_drop_total = enrichment.backlog_drop_total
enrichment_timeout_total = enrichment.timeout_total

# Worker / background job metrics
worker_jobs_total = worker_metrics_group.jobs_total
worker_job_duration_seconds = worker_metrics_group.job_duration_seconds
worker_queue_depth = worker_metrics_group.queue_depth
worker_queue_oldest_job_age_seconds = worker_metrics_group.queue_oldest_job_age_seconds
local_enrichment_pending = worker_metrics_group.local_enrichment_pending

# Logging pipeline metrics
logging_dropped_total = logging_metrics_group.dropped_total


# ── Label cardinality helpers ────────────────────────────────────────
# Cap the set of exception class names that can become Prometheus label
# values so a misbehaving dependency raising dynamically-named classes
# cannot cause label-explosion on ``application_errors_total``. Anything
# not in this allow-list is bucketed as ``"other"``.
_KNOWN_ERROR_TYPES = frozenset(
    {
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "RuntimeError",
        "TimeoutError",
        "CancelledError",
        "ConnectionError",
        "ConnectionResetError",
        "OSError",
        "IOError",
        "DatabaseError",
        "OperationalError",
        "IntegrityError",
        "InterfaceError",
        "ProgrammingError",
        "PoolError",
        "HTTPError",
        "RequestError",
        "HTTPStatusError",
        "ReadTimeout",
        "ConnectTimeout",
        "JSONDecodeError",
        "NotImplementedError",
        "AssertionError",
        "LookupError",
        "IndexError",
    }
)


def bucket_error_type(error_type: str) -> str:
    """Bucket an exception class name for low-cardinality labeling.

    Names in the allow-list pass through unchanged; everything else is
    collapsed to ``"other"`` to bound Prometheus label cardinality. Used
    by ``application_errors_total`` in ``app.py``.
    """
    if not error_type:
        return "other"
    if error_type in _KNOWN_ERROR_TYPES:
        return error_type
    return "other"


def bucket_http_status(status_code) -> str:
    """Collapse raw HTTP status codes into low-cardinality buckets.

    - ``"circuit_open"`` / ``"transport_error"`` / ``"error"`` pass through.
    - ``"429"`` passes through (rate limiting is operationally important).
    - ``2xx`` / ``3xx`` / ``4xx`` / ``5xx`` are bucketed by class.
    """
    if status_code is None:
        return "other"
    text = str(status_code)
    if not text.isdigit():
        return text  # circuit_open, transport_error, error
    if text == "429":
        return "429"
    try:
        code = int(text)
    except ValueError:  # pragma: no cover - defensive
        return "other"
    if 200 <= code < 300:
        return "2xx"
    if 300 <= code < 400:
        return "3xx"
    if 400 <= code < 500:
        return "4xx"
    if 500 <= code < 600:
        return "5xx"
    return "other"


# ============================================================================
# METRICS COLLECTION SERVICE
# ============================================================================


class MetricsCollector:
    """Service to collect and expose metrics"""

    def __init__(self, db_pool=None, movie_manager=None):
        self.db_pool = db_pool
        self.movie_manager = movie_manager
        self._collection_task: Optional[asyncio.Task] = None
        self._active_user_timeout = 1800  # 30 minutes
        self._max_tracked_users = 10000  # Cap to prevent unbounded growth
        # LruExpiringMap handles LRU eviction + TTL expiration in one structure.
        # Uses wall-clock time so raw timestamps stored by callers are directly
        # comparable against time.time() in the eviction scan.
        self._active_users: LruExpiringMap = LruExpiringMap(
            max_keys=self._max_tracked_users,
            ttl_seconds=self._active_user_timeout,
            time_func=time.time,
        )
        self.logger = get_logger(__name__)

    async def start_collection(self):
        """Start background metrics collection"""
        if self._collection_task is None:
            self._collection_task = asyncio.create_task(self._collect_metrics())
            self.logger.info("Metrics collection started")

    async def stop_collection(self):
        """Stop metrics collection"""
        if self._collection_task:
            self._collection_task.cancel()
            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass
            self._collection_task = None
            self.logger.info("Metrics collection stopped")

    async def _collect_metrics(self):
        """Background task to collect metrics"""
        while True:
            try:
                # Collect database pool metrics
                await self._collect_db_metrics()

                # Collect movie queue metrics
                await self._collect_movie_metrics()

                # Evict stale users and update active users count
                now = time.time()
                snapshot = dict(self._active_users)
                stale = [
                    uid for uid, ts in snapshot.items() if now - ts > self._active_user_timeout
                ]
                for uid in stale:
                    self._active_users.pop(uid, None)
                active_users.set(len(self._active_users))

                # Sleep for 10 seconds before next collection
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Error collecting metrics: %s", e)
                await asyncio.sleep(10)

    async def _collect_db_metrics(self):
        """Collect database metrics"""
        if not self.db_pool:
            return

        try:
            metrics = await self.db_pool.get_metrics()

            # Pool size metrics
            db_connections_active.set(
                metrics.get("pool_size", 0) - metrics.get("free_connections", 0)
            )
            db_connections_idle.set(metrics.get("free_connections", 0))
            db_connections_total.set(metrics.get("pool_size", 0))

            state = metrics.get("circuit_breaker_state", "closed")
            db_circuit_breaker_state.set(_CIRCUIT_STATE_METRIC_VALUES.get(state, 0))

        except Exception as e:
            self.logger.error("Failed to collect database metrics: %s", e)

    async def _collect_movie_metrics(self):
        """Collect movie-related metrics — currently a no-op.

        Queue sizes are per-session (stored in Redis) and cannot be
        aggregated cheaply from a background task.  Individual request
        metrics are captured by the middleware instead.
        """

    def track_user_activity(self, user_id: str):
        """Track user activity with timestamp for expiry.

        LruExpiringMap handles LRU + TTL automatically; set() refreshes
        both ordering and TTL and evicts the oldest entry when at cap.
        """
        self._active_users[user_id] = time.time()

    def track_user_action(self, action_type: str):
        """Track user actions"""
        user_actions_total.labels(action_type=action_type).inc()

    def track_movie_recommendation(self, recommendation_type: str = "default"):
        """Track movie recommendations"""
        movie_recommendations_total.labels(recommendation_type=recommendation_type).inc()


# ============================================================================
# METRICS ENDPOINT
# ============================================================================


async def metrics_endpoint():
    """Quart endpoint to expose Prometheus metrics"""
    try:
        metrics_data = generate_latest(REGISTRY)
        return Response(
            metrics_data.decode("utf-8"), mimetype="text/plain; version=0.0.4; charset=utf-8"
        )
    except Exception as e:
        logger.error("Failed to generate metrics: %s", e)
        return Response("Error generating metrics", status=500)


# ============================================================================
# MIDDLEWARE INTEGRATION
# ============================================================================


def setup_metrics_middleware(app, metrics_collector: MetricsCollector):
    """Setup metrics collection middleware for Quart app"""

    @app.before_request
    async def before_request():
        g.start_time = time.time()
        http_requests_in_progress.inc()

        # Track user activity
        nav_state = getattr(g, "navigation_state", None)
        if nav_state and getattr(nav_state, "session_id", None):
            metrics_collector.track_user_activity(nav_state.session_id)

    @app.after_request
    async def after_request(response):
        try:
            if hasattr(g, "start_time"):
                duration = time.time() - g.start_time

                http_requests_total.labels(
                    method=request.method,
                    endpoint=request.endpoint or "unknown",
                    status_code=bucket_http_status(response.status_code),
                ).inc()

                http_request_duration_seconds.labels(
                    method=request.method, endpoint=request.endpoint or "unknown"
                ).observe(duration)

            http_requests_in_progress.dec()

        except Exception as e:
            logger.error("Error in metrics middleware: %s", e)

        return response


def set_rate_limit_backend(backend: str) -> None:
    for candidate in ("redis", "memory"):
        rate_limit_backend_mode.labels(backend=candidate).set(1 if candidate == backend else 0)
