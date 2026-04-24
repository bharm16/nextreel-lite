from __future__ import annotations


class TMDbMetricsRecorder:
    """Best-effort Prometheus emission for TMDb transport outcomes."""

    def record_api_call(self, logical_endpoint, status_code, duration_seconds) -> None:
        from infra.metrics import (
            bucket_http_status,
            tmdb_api_calls_total,
            tmdb_api_duration_seconds,
        )
        from infra.metrics_groups import safe_emit

        safe_emit(
            lambda: tmdb_api_calls_total.labels(
                endpoint=logical_endpoint,
                status_code=bucket_http_status(status_code),
            ).inc()
        )
        safe_emit(
            tmdb_api_duration_seconds.labels(endpoint=logical_endpoint).observe,
            duration_seconds,
        )

    def record_rate_limit(self, response) -> None:
        try:
            remaining = response.headers.get("X-RateLimit-Remaining")
        except Exception:  # pragma: no cover - best-effort
            return
        if remaining is None:
            return
        from infra.metrics import tmdb_rate_limit_remaining
        from infra.metrics_groups import safe_emit

        try:
            value = float(remaining)
        except (TypeError, ValueError):
            return
        safe_emit(tmdb_rate_limit_remaining.set, value)
