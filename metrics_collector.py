"""
Prometheus metrics collection for Nextreel application.
Provides detailed metrics that integrate with Grafana Cloud.
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY, Info
from functools import wraps
import time
import asyncio
from typing import Callable, Optional, Dict, Any
from quart import Response, request, g, session
import logging
import os

# Application info metric
app_info = Info('nextreel_app_info', 'Application information')
app_info.info({
    'version': os.getenv('APP_VERSION', '1.0.0'),
    'environment': os.getenv('FLASK_ENV', 'development')
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

movie_queue_size = Gauge(
    'nextreel_movie_queue_size',
    'Current movie queue size',
    ['user_id']
)

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

# ============================================================================
# ERROR METRICS
# ============================================================================

application_errors_total = Counter(
    'nextreel_application_errors_total',
    'Total application errors',
    ['error_type', 'endpoint']
)

# ============================================================================
# DECORATOR FUNCTIONS
# ============================================================================

def track_request_metrics(func: Callable) -> Callable:
    """Decorator to track HTTP request metrics"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        http_requests_in_progress.inc()
        
        method = request.method if hasattr(request, 'method') else 'GET'
        endpoint = func.__name__
        status_code = 200
        
        try:
            response = await func(*args, **kwargs)
            
            # Extract status code from response
            if isinstance(response, tuple):
                status_code = response[1] if len(response) > 1 else 200
            elif hasattr(response, 'status_code'):
                status_code = response.status_code
                
        except Exception as e:
            status_code = 500
            application_errors_total.labels(
                error_type=type(e).__name__,
                endpoint=endpoint
            ).inc()
            raise
        finally:
            duration = time.time() - start_time
            http_requests_in_progress.dec()
            
            # Record metrics
            http_requests_total.labels(
                method=method,
                endpoint=endpoint,
                status_code=str(status_code)
            ).inc()
            
            http_request_duration_seconds.labels(
                method=method,
                endpoint=endpoint
            ).observe(duration)
            
        return response
    return wrapper


def track_db_metrics(query_type: str, table: str = 'unknown'):
    """Decorator to track database query metrics"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = 'success'
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = 'error'
                raise
            finally:
                duration = time.time() - start_time
                db_queries_total.labels(
                    query_type=query_type, 
                    table=table,
                    status=status
                ).inc()
                db_query_duration_seconds.labels(query_type=query_type).observe(duration)
                
        return wrapper
    return decorator


def track_tmdb_api_call(endpoint: str):
    """Decorator to track TMDB API calls"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status_code = '200'
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status_code = '500'
                raise
            finally:
                duration = time.time() - start_time
                tmdb_api_calls_total.labels(
                    endpoint=endpoint,
                    status_code=status_code
                ).inc()
                tmdb_api_duration_seconds.labels(endpoint=endpoint).observe(duration)
                
        return wrapper
    return decorator


def track_cache_operation(operation: str, cache_type: str):
    """Decorator to track cache operations"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                # Assume cache hit if result is not None
                if result is not None:
                    cache_hits_total.labels(cache_type=cache_type).inc()
                else:
                    cache_misses_total.labels(cache_type=cache_type).inc()
                return result
            finally:
                duration = time.time() - start_time
                cache_operations_duration_seconds.labels(
                    operation=operation,
                    cache_type=cache_type
                ).observe(duration)
                
        return wrapper
    return decorator


# ============================================================================
# METRICS COLLECTION SERVICE
# ============================================================================

class MetricsCollector:
    """Service to collect and expose metrics"""
    
    def __init__(self, db_pool=None, movie_manager=None):
        self.db_pool = db_pool
        self.movie_manager = movie_manager
        self._collection_task: Optional[asyncio.Task] = None
        self._active_users: set = set()
        self.logger = logging.getLogger(__name__)
        
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
                
                # Update active users count
                active_users.set(len(self._active_users))
                
                # Sleep for 10 seconds before next collection
                await asyncio.sleep(10)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error collecting metrics: {e}")
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
            self.logger.error(f"Failed to collect database metrics: {e}")
    
    async def _collect_movie_metrics(self):
        """Collect movie-related metrics"""
        if not self.movie_manager:
            return
            
        try:
            # This would depend on your MovieManager implementation
            # You might need to add methods to expose queue sizes, etc.
            pass
        except Exception as e:
            self.logger.error(f"Failed to collect movie metrics: {e}")
    
    def track_user_activity(self, user_id: str):
        """Track user activity"""
        self._active_users.add(user_id)
    
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
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to generate metrics: {e}")
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
        if 'user_id' in session:
            metrics_collector.track_user_activity(session['user_id'])
    
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
            logger = logging.getLogger(__name__)
            logger.error(f"Error in metrics middleware: {e}")
        
        return response


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_metrics_summary() -> Dict[str, Any]:
    """Get a summary of current metrics"""
    return {
        'http_requests_total': http_requests_total._value._value,
        'db_connections_active': db_connections_active._value._value,
        'db_connections_idle': db_connections_idle._value._value,
        'active_users': active_users._value._value,
        'movie_recommendations_total': movie_recommendations_total._value._value,
    }