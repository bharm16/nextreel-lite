"""
Prometheus metrics collection for Nextreel application.
Provides detailed metrics that integrate with Grafana Cloud.
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY, Info
import time
import asyncio
from typing import Optional, Dict, Any
from quart import Response, request, g
from logging_config import get_logger
import os

# Application info metric
app_info = Info('nextreel_app_info', 'Application information')
app_info.info({
    'version': os.getenv('APP_VERSION', '1.0.0'),
    'environment': os.getenv('NEXTREEL_ENV', os.getenv('FLASK_ENV', 'production'))
})

# ============================================================================
# HTTP METRICS
# ============================================================================

http_requests_total = Counter(
    'nextreel_http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code']
)

http_request_duration_seconds = Histogram(
    'nextreel_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint'],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)
)

http_requests_in_progress = Gauge(
    'nextreel_http_requests_in_progress',
    'Number of HTTP requests currently being processed'
)

# ============================================================================
# DATABASE METRICS
# ============================================================================

db_connections_active = Gauge(
    'nextreel_db_connections_active',
    'Active database connections'
)

db_connections_idle = Gauge(
    'nextreel_db_connections_idle',
    'Idle database connections'
)

db_connections_total = Gauge(
    'nextreel_db_connections_total',
    'Total database connections in pool'
)

db_queries_total = Counter(
    'nextreel_db_queries_total',
    'Total database queries',
    ['query_type', 'table', 'status']
)

db_query_duration_seconds = Histogram(
    'nextreel_db_query_duration_seconds',
    'Database query duration in seconds',
    ['query_type'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
)

db_circuit_breaker_state = Gauge(
    'nextreel_db_circuit_breaker_state',
    'Database circuit breaker state (0=closed, 1=open, 2=half-open)'
)

db_connection_errors_total = Counter(
    'nextreel_db_connection_errors_total',
    'Total database connection errors'
)

# ============================================================================
# MOVIE & RECOMMENDATION METRICS
# ============================================================================

movie_recommendations_total = Counter(
    'nextreel_movie_recommendations_total',
    'Total movie recommendations served',
    ['recommendation_type']
)

movie_fetches_total = Counter(
    'nextreel_movie_fetches_total',
    'Total movie data fetches',
    ['source', 'status']
)

movie_filters_applied_total = Counter(
    'nextreel_movie_filters_applied_total',
    'Total movie filters applied',
    ['filter_type']
)

# ============================================================================
# TMDB API METRICS
# ============================================================================

tmdb_api_calls_total = Counter(
    'nextreel_tmdb_api_calls_total',
    'Total TMDB API calls',
    ['endpoint', 'status_code']
)

tmdb_api_duration_seconds = Histogram(
    'nextreel_tmdb_api_duration_seconds',
    'TMDB API call duration in seconds',
    ['endpoint']
)

tmdb_rate_limit_remaining = Gauge(
    'nextreel_tmdb_rate_limit_remaining',
    'Remaining TMDB API rate limit'
)

# ============================================================================
# USER & SESSION METRICS
# ============================================================================

active_users = Gauge(
    'nextreel_active_users',
    'Currently active users'
)

user_sessions_total = Counter(
    'nextreel_user_sessions_total',
    'Total user sessions created'
)

user_actions_total = Counter(
    'nextreel_user_actions_total',
    'Total user actions',
    ['action_type']
)

session_duration_seconds = Histogram(
    'nextreel_session_duration_seconds',
    'User session duration in seconds'
)

# ============================================================================
# CACHE METRICS
# ============================================================================

cache_hits_total = Counter(
    'nextreel_cache_hits_total',
    'Total cache hits',
    ['cache_type']
)

cache_misses_total = Counter(
    'nextreel_cache_misses_total',
    'Total cache misses',
    ['cache_type']
)

cache_operations_duration_seconds = Histogram(
    'nextreel_cache_operations_duration_seconds',
    'Cache operation duration in seconds',
    ['operation', 'cache_type']
)

rate_limit_backend_mode = Gauge(
    'nextreel_rate_limit_backend_mode',
    'Current rate limiter backend mode (1 active, 0 inactive)',
    ['backend']
)

# ============================================================================
# ERROR METRICS
# ============================================================================

application_errors_total = Counter(
    'nextreel_application_errors_total',
    'Total application errors',
    ['error_type', 'endpoint']
)

navigation_state_redis_import_total = Counter(
    'nextreel_navigation_state_redis_import_total',
    'Total successful legacy Redis session imports into MySQL navigation state'
)

navigation_state_migration_miss_total = Counter(
    'nextreel_navigation_state_migration_miss_total',
    'Total migration misses when no legacy Redis session could be imported'
)

navigation_state_conflicts_total = Counter(
    'nextreel_navigation_state_conflicts_total',
    'Total optimistic concurrency conflicts while mutating navigation state'
)

home_prewarm_failed_total = Counter(
    'nextreel_home_prewarm_failed_total',
    'Total failed home page queue prewarm attempts'
)

# ============================================================================
# METRICS COLLECTION SERVICE
# ============================================================================

class MetricsCollector:
    """Service to collect and expose metrics"""
    
    def __init__(self, db_pool=None, movie_manager=None):
        self.db_pool = db_pool
        self.movie_manager = movie_manager
        self._collection_task: Optional[asyncio.Task] = None
        self._active_users: dict = {}  # user_id -> last_seen_timestamp
        self._active_user_timeout = 1800  # 30 minutes
        self._max_tracked_users = 10000  # Cap to prevent unbounded growth
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
                stale = [
                    uid for uid, ts in self._active_users.items()
                    if now - ts > self._active_user_timeout
                ]
                for uid in stale:
                    del self._active_users[uid]
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
                metrics.get('pool_size', 0) - metrics.get('free_connections', 0)
            )
            db_connections_idle.set(metrics.get('free_connections', 0))
            db_connections_total.set(metrics.get('pool_size', 0))
            
            # Circuit breaker state
            state_map = {'closed': 0, 'open': 1, 'half-open': 2}
            state = metrics.get('circuit_breaker_state', 'closed')
            db_circuit_breaker_state.set(state_map.get(state, 0))
            
        except Exception as e:
            self.logger.error("Failed to collect database metrics: %s", e)
    
    async def _collect_movie_metrics(self):
        """Collect movie-related metrics — currently a no-op.

        Queue sizes are per-session (stored in Redis) and cannot be
        aggregated cheaply from a background task.  Individual request
        metrics are captured by the middleware instead.
        """
    
    def track_user_activity(self, user_id: str):
        """Track user activity with timestamp for expiry."""
        self._active_users[user_id] = time.time()
        # Evict oldest entries if we exceed the cap
        if len(self._active_users) > self._max_tracked_users:
            cutoff = time.time() - self._active_user_timeout
            stale = [uid for uid, ts in self._active_users.items() if ts < cutoff]
            for uid in stale:
                del self._active_users[uid]
    
    def track_user_action(self, action_type: str):
        """Track user actions"""
        user_actions_total.labels(action_type=action_type).inc()
    
    def track_movie_recommendation(self, recommendation_type: str = 'default'):
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
            metrics_data.decode('utf-8'), 
            mimetype='text/plain; version=0.0.4; charset=utf-8'
        )
    except Exception as e:
        logger = get_logger(__name__)
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
        nav_state = getattr(g, 'navigation_state', None)
        if nav_state and getattr(nav_state, 'session_id', None):
            metrics_collector.track_user_activity(nav_state.session_id)
    
    @app.after_request
    async def after_request(response):
        try:
            if hasattr(g, 'start_time'):
                duration = time.time() - g.start_time
                
                http_requests_total.labels(
                    method=request.method,
                    endpoint=request.endpoint or 'unknown',
                    status_code=str(response.status_code)
                ).inc()
                
                http_request_duration_seconds.labels(
                    method=request.method,
                    endpoint=request.endpoint or 'unknown'
                ).observe(duration)
                
            http_requests_in_progress.dec()
            
        except Exception as e:
            logger = get_logger(__name__)
            logger.error("Error in metrics middleware: %s", e)
        
        return response


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _gauge_value(gauge):
    """Safely read a Gauge's current value via the public collect() API."""
    for metric in gauge.collect():
        for sample in metric.samples:
            return sample.value
    return 0


def get_metrics_summary() -> Dict[str, Any]:
    """Get a summary of current metrics"""
    return {
        'db_connections_active': _gauge_value(db_connections_active),
        'db_connections_idle': _gauge_value(db_connections_idle),
        'active_users': _gauge_value(active_users),
    }


def set_rate_limit_backend(backend: str) -> None:
    for candidate in ("redis", "memory"):
        rate_limit_backend_mode.labels(backend=candidate).set(1 if candidate == backend else 0)
