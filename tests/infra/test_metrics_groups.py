"""Tests for organized metrics groups."""

import logging
from unittest.mock import Mock

from infra.metrics_groups import (
    HTTPMetrics,
    DatabaseMetrics,
    MovieMetrics,
    CacheMetrics,
    ErrorMetrics,
    http,
    database,
    movie,
    cache,
    error,
    safe_emit,
)


def test_safe_emit_returns_result_on_success():
    fn = Mock(return_value="ok")
    assert safe_emit(fn, 1, 2, k="v") == "ok"
    fn.assert_called_once_with(1, 2, k="v")


def test_safe_emit_returns_none_on_exception():
    fn = Mock(side_effect=RuntimeError("boom"))
    assert safe_emit(fn, "a") is None


def test_safe_emit_logs_at_debug_on_exception(caplog):
    fn = Mock(side_effect=ValueError("bad label"))
    with caplog.at_level(logging.DEBUG, logger="infra.metrics_groups"):
        safe_emit(fn)
    assert any("metric emit failed" in r.message for r in caplog.records)


def test_safe_emit_no_args():
    fn = Mock(return_value=42)
    assert safe_emit(fn) == 42
    fn.assert_called_once_with()


def test_http_metrics_group_has_expected_fields():
    assert hasattr(http, "requests_total")
    assert hasattr(http, "request_duration_seconds")
    assert hasattr(http, "requests_in_progress")


def test_database_metrics_group_has_expected_fields():
    assert hasattr(database, "connections_active")
    assert hasattr(database, "circuit_breaker_state")
    assert hasattr(database, "queries_total")


def test_movie_metrics_group():
    assert hasattr(movie, "recommendations_total")
    assert hasattr(movie, "fetches_total")


def test_cache_metrics_group():
    assert hasattr(cache, "hits_total")
    assert hasattr(cache, "misses_total")


def test_error_metrics_group():
    assert hasattr(error, "application_errors_total")
    assert hasattr(error, "navigation_state_conflicts_total")
    assert hasattr(error, "home_prewarm_failed_total")


def test_backward_compat_aliases():
    """Verify metrics.py re-exports match the grouped originals."""
    from infra.metrics import (
        http_requests_total,
        db_connections_active,
        movie_recommendations_total,
        cache_hits_total,
        application_errors_total,
        user_actions_total,
    )

    assert http_requests_total is http.requests_total
    assert db_connections_active is database.connections_active
    assert movie_recommendations_total is movie.recommendations_total
    assert cache_hits_total is cache.hits_total
    assert application_errors_total is error.application_errors_total
