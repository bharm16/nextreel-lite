"""Grouped metric registrations — organizes Prometheus metrics by domain.

Replaces the flat list of 30+ module-level globals in metrics.py with
cohesive metric groups.  Each group is a simple namespace dataclass.
The actual Prometheus objects are still module-level singletons (required
by prometheus_client), but they are now discoverable by domain.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram, Info

from config.env import get_environment


# ── Application info ─────────────────────────────────────────────────

app_info = Info("nextreel_app_info", "Application information")
app_info.info({
    "version": os.getenv("APP_VERSION", "1.0.0"),
    "environment": get_environment(),
})


# ── HTTP ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HTTPMetrics:
    requests_total: Counter
    request_duration_seconds: Histogram
    requests_in_progress: Gauge


http = HTTPMetrics(
    requests_total=Counter(
        "nextreel_http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status_code"],
    ),
    request_duration_seconds=Histogram(
        "nextreel_http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "endpoint"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0),
    ),
    requests_in_progress=Gauge(
        "nextreel_http_requests_in_progress",
        "Number of HTTP requests currently being processed",
    ),
)


# ── Database ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DatabaseMetrics:
    connections_active: Gauge
    connections_idle: Gauge
    connections_total: Gauge
    queries_total: Counter
    query_duration_seconds: Histogram
    circuit_breaker_state: Gauge
    connection_errors_total: Counter


database = DatabaseMetrics(
    connections_active=Gauge("nextreel_db_connections_active", "Active database connections"),
    connections_idle=Gauge("nextreel_db_connections_idle", "Idle database connections"),
    connections_total=Gauge("nextreel_db_connections_total", "Total database connections in pool"),
    queries_total=Counter("nextreel_db_queries_total", "Total database queries", ["query_type", "table", "status"]),
    query_duration_seconds=Histogram(
        "nextreel_db_query_duration_seconds",
        "Database query duration in seconds",
        ["query_type"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    ),
    circuit_breaker_state=Gauge(
        "nextreel_db_circuit_breaker_state",
        "Database circuit breaker state (0=closed, 1=open, 2=half-open)",
    ),
    connection_errors_total=Counter("nextreel_db_connection_errors_total", "Total database connection errors"),
)


# ── Movie & recommendations ──────────────────────────────────────────

@dataclass(frozen=True)
class MovieMetrics:
    recommendations_total: Counter
    fetches_total: Counter
    filters_applied_total: Counter


movie = MovieMetrics(
    recommendations_total=Counter(
        "nextreel_movie_recommendations_total",
        "Total movie recommendations served",
        ["recommendation_type"],
    ),
    fetches_total=Counter("nextreel_movie_fetches_total", "Total movie data fetches", ["source", "status"]),
    filters_applied_total=Counter("nextreel_movie_filters_applied_total", "Total movie filters applied", ["filter_type"]),
)


# ── TMDb API ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TMDbMetrics:
    api_calls_total: Counter
    api_duration_seconds: Histogram
    rate_limit_remaining: Gauge


tmdb = TMDbMetrics(
    api_calls_total=Counter("nextreel_tmdb_api_calls_total", "Total TMDB API calls", ["endpoint", "status_code"]),
    api_duration_seconds=Histogram("nextreel_tmdb_api_duration_seconds", "TMDB API call duration in seconds", ["endpoint"]),
    rate_limit_remaining=Gauge("nextreel_tmdb_rate_limit_remaining", "Remaining TMDB API rate limit"),
)


# ── User & session ───────────────────────────────────────────────────

@dataclass(frozen=True)
class UserMetrics:
    active_users: Gauge
    sessions_total: Counter
    actions_total: Counter
    session_duration_seconds: Histogram


user = UserMetrics(
    active_users=Gauge("nextreel_active_users", "Currently active users"),
    sessions_total=Counter("nextreel_user_sessions_total", "Total user sessions created"),
    actions_total=Counter("nextreel_user_actions_total", "Total user actions", ["action_type"]),
    session_duration_seconds=Histogram("nextreel_session_duration_seconds", "User session duration in seconds"),
)


# ── Cache ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CacheMetrics:
    hits_total: Counter
    misses_total: Counter
    operations_duration_seconds: Histogram
    rate_limit_backend_mode: Gauge


cache = CacheMetrics(
    hits_total=Counter("nextreel_cache_hits_total", "Total cache hits", ["cache_type"]),
    misses_total=Counter("nextreel_cache_misses_total", "Total cache misses", ["cache_type"]),
    operations_duration_seconds=Histogram(
        "nextreel_cache_operations_duration_seconds",
        "Cache operation duration in seconds",
        ["operation", "cache_type"],
    ),
    rate_limit_backend_mode=Gauge(
        "nextreel_rate_limit_backend_mode",
        "Current rate limiter backend mode (1 active, 0 inactive)",
        ["backend"],
    ),
)


# ── Error / operational ──────────────────────────────────────────────

@dataclass(frozen=True)
class ErrorMetrics:
    application_errors_total: Counter
    navigation_state_redis_import_total: Counter
    navigation_state_migration_miss_total: Counter
    navigation_state_conflicts_total: Counter
    home_prewarm_failed_total: Counter


error = ErrorMetrics(
    application_errors_total=Counter(
        "nextreel_application_errors_total",
        "Total application errors",
        ["error_type", "endpoint"],
    ),
    navigation_state_redis_import_total=Counter(
        "nextreel_navigation_state_redis_import_total",
        "Total successful legacy Redis session imports into MySQL navigation state",
    ),
    navigation_state_migration_miss_total=Counter(
        "nextreel_navigation_state_migration_miss_total",
        "Total migration misses when no legacy Redis session could be imported",
    ),
    navigation_state_conflicts_total=Counter(
        "nextreel_navigation_state_conflicts_total",
        "Total optimistic concurrency conflicts while mutating navigation state",
    ),
    home_prewarm_failed_total=Counter(
        "nextreel_home_prewarm_failed_total",
        "Total failed home page queue prewarm attempts",
    ),
)
