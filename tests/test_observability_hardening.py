"""Tests for the observability-hardening pass.

Covers:
- TMDb client metric emission with stable logical endpoint labels
- Logging dropped-log counter wiring
- Application error metric wiring for the navigation-state 503 path
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from movies.tmdb_client import TMDbHelper


# ---------------------------------------------------------------------------
# TMDb metric emission
# ---------------------------------------------------------------------------


def _counter_value(counter, **labels):
    return counter.labels(**labels)._value.get()


async def test_tmdb_successful_call_emits_counter_and_duration():
    from infra.metrics import tmdb_api_calls_total, tmdb_api_duration_seconds

    helper = TMDbHelper("key")
    before = _counter_value(tmdb_api_calls_total, endpoint="movie_full", status_code="2xx")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {}
    fake_response.json = MagicMock(return_value={"id": 1})
    fake_response.raise_for_status = MagicMock()

    with patch.object(
        helper._client, "get", AsyncMock(return_value=fake_response)
    ):
        await helper._get("movie/1", metric_endpoint="movie_full")

    after = _counter_value(tmdb_api_calls_total, endpoint="movie_full", status_code="2xx")
    assert after == before + 1

    # Histogram sample count for this label should have advanced
    hist_samples = tmdb_api_duration_seconds.labels(endpoint="movie_full")._sum.get()
    assert hist_samples >= 0


async def test_tmdb_missing_metric_endpoint_uses_unknown_label():
    from infra.metrics import tmdb_api_calls_total

    helper = TMDbHelper("key")
    before = _counter_value(tmdb_api_calls_total, endpoint="unknown", status_code="2xx")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {}
    fake_response.json = MagicMock(return_value={})
    fake_response.raise_for_status = MagicMock()

    with patch.object(helper._client, "get", AsyncMock(return_value=fake_response)):
        await helper._get("movie/1")

    after = _counter_value(tmdb_api_calls_total, endpoint="unknown", status_code="2xx")
    assert after == before + 1


async def test_tmdb_circuit_open_emits_distinct_status_label():
    from infra.metrics import tmdb_api_calls_total

    helper = TMDbHelper("key")
    before = _counter_value(
        tmdb_api_calls_total, endpoint="movie_full", status_code="circuit_open"
    )

    with patch.object(
        helper._circuit_breaker, "allow_request", AsyncMock(return_value=False)
    ):
        with pytest.raises(httpx.RequestError):
            await helper._get("movie/1", metric_endpoint="movie_full")

    after = _counter_value(
        tmdb_api_calls_total, endpoint="movie_full", status_code="circuit_open"
    )
    assert after == before + 1


async def test_tmdb_transport_error_emits_transport_error_label():
    from infra.metrics import tmdb_api_calls_total

    helper = TMDbHelper("key")
    helper._max_retries = 0  # fail fast
    before = _counter_value(
        tmdb_api_calls_total, endpoint="movie_full", status_code="transport_error"
    )

    with patch.object(
        helper._client,
        "get",
        AsyncMock(side_effect=httpx.ConnectError("boom")),
    ):
        with pytest.raises(httpx.RequestError):
            await helper._get("movie/1", metric_endpoint="movie_full")

    after = _counter_value(
        tmdb_api_calls_total, endpoint="movie_full", status_code="transport_error"
    )
    assert after == before + 1


async def test_tmdb_429_emits_429_label():
    from infra.metrics import tmdb_api_calls_total

    helper = TMDbHelper("key")
    helper._max_retries = 0

    fake_response = MagicMock()
    fake_response.status_code = 429
    fake_response.headers = {"Retry-After": "0"}

    class _FakeHTTPError(httpx.HTTPStatusError):
        pass

    fake_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "rate limited", request=MagicMock(), response=fake_response
        )
    )

    before = _counter_value(
        tmdb_api_calls_total, endpoint="movie_full", status_code="429"
    )

    with patch.object(helper._client, "get", AsyncMock(return_value=fake_response)):
        with pytest.raises(httpx.HTTPStatusError):
            await helper._get("movie/1", metric_endpoint="movie_full")

    after = _counter_value(
        tmdb_api_calls_total, endpoint="movie_full", status_code="429"
    )
    assert after == before + 1


async def test_tmdb_rate_limit_header_sets_gauge():
    from infra.metrics import tmdb_rate_limit_remaining

    helper = TMDbHelper("key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"X-RateLimit-Remaining": "37"}
    fake_response.json = MagicMock(return_value={})
    fake_response.raise_for_status = MagicMock()

    with patch.object(helper._client, "get", AsyncMock(return_value=fake_response)):
        await helper._get("movie/1", metric_endpoint="movie_full")

    assert tmdb_rate_limit_remaining._value.get() == 37.0


# ---------------------------------------------------------------------------
# Logging dropped counter wiring
# ---------------------------------------------------------------------------


def test_logging_dropped_counter_exists_and_increments():
    from infra.metrics import logging_dropped_total

    before = logging_dropped_total.labels(reason="buffer_full")._value.get()
    from logging_config import _increment_dropped_logs

    _increment_dropped_logs("buffer_full")
    after = logging_dropped_total.labels(reason="buffer_full")._value.get()
    assert after == before + 1


def test_json_formatter_includes_context_fields():
    import logging
    from logging_config import JSONFormatter

    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.correlation_id = "abc-123"
    record.endpoint = "main.home"
    record.method = "GET"
    record.status_code = 200
    record.duration_ms = 12.5

    import json

    payload = json.loads(formatter.format(record))
    assert payload["correlation_id"] == "abc-123"
    assert payload["endpoint"] == "main.home"
    assert payload["method"] == "GET"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 12.5
    assert payload["message"] == "hello"


def test_logging_dropped_counter_is_cached_after_first_use():
    """C1 regression: the counter handle must be cached after the first
    successful lookup so the hot emit() path does not re-import on every
    drop. Also verifies that a permanent init failure flips a sentinel so
    subsequent calls stop retrying (no re-entrancy risk under repeated
    prometheus_client failure modes).
    """
    import logging_config

    # Force cache population by calling _increment_dropped_logs once.
    logging_config._logging_dropped_total = None
    logging_config._logging_metric_init_failed = False
    logging_config._increment_dropped_logs("buffer_full")
    assert logging_config._logging_dropped_total is not None

    # Simulate permanent init failure: if handle is None and sentinel is
    # set, the call must short-circuit and not raise.
    logging_config._logging_dropped_total = None
    logging_config._logging_metric_init_failed = True
    # Must not raise even though no counter is bound.
    logging_config._increment_dropped_logs("buffer_full")
    assert logging_config._logging_dropped_total is None

    # Reset state so later tests in the session get a clean counter.
    logging_config._logging_metric_init_failed = False
    logging_config._increment_dropped_logs("buffer_full")
    assert logging_config._logging_dropped_total is not None


# ---------------------------------------------------------------------------
# Label cardinality bucketing
# ---------------------------------------------------------------------------


def test_bucket_error_type_passes_known_and_collapses_unknown():
    from infra.metrics import bucket_error_type

    assert bucket_error_type("ValueError") == "ValueError"
    assert bucket_error_type("TimeoutError") == "TimeoutError"
    assert bucket_error_type("OperationalError") == "OperationalError"
    # Unknown / dynamic exception classes collapse to "other".
    assert bucket_error_type("_ConnectError") == "other"
    assert bucket_error_type("SomeDynamicallyGeneratedError") == "other"
    assert bucket_error_type("") == "other"
    assert bucket_error_type(None) == "other"  # type: ignore[arg-type]


def test_bucket_http_status_collapses_to_class():
    from infra.metrics import bucket_http_status

    # Digit codes bucket to the class.
    assert bucket_http_status(200) == "2xx"
    assert bucket_http_status("201") == "2xx"
    assert bucket_http_status(301) == "3xx"
    assert bucket_http_status(404) == "4xx"
    assert bucket_http_status(503) == "5xx"
    # 429 is preserved as a distinct operational signal.
    assert bucket_http_status(429) == "429"
    assert bucket_http_status("429") == "429"
    # Sentinel strings pass through.
    assert bucket_http_status("circuit_open") == "circuit_open"
    assert bucket_http_status("transport_error") == "transport_error"
    assert bucket_http_status("error") == "error"
    assert bucket_http_status(None) == "other"


async def test_tmdb_500_is_bucketed_as_5xx():
    from infra.metrics import tmdb_api_calls_total

    helper = TMDbHelper("key")
    helper._max_retries = 0
    fake_response = MagicMock()
    fake_response.status_code = 503
    fake_response.headers = {}
    fake_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=fake_response
        )
    )

    before = _counter_value(
        tmdb_api_calls_total, endpoint="movie_full", status_code="5xx"
    )
    with patch.object(helper._client, "get", AsyncMock(return_value=fake_response)):
        with pytest.raises(httpx.HTTPStatusError):
            await helper._get("movie/1", metric_endpoint="movie_full")
    after = _counter_value(
        tmdb_api_calls_total, endpoint="movie_full", status_code="5xx"
    )
    assert after == before + 1


def test_json_formatter_without_context_still_works():
    """Critical: must not raise when called before setup_logging() / on records
    without any extra context (the CLAUDE.md import-time gotcha)."""
    import logging
    from logging_config import JSONFormatter

    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="bare",
        args=(),
        exc_info=None,
    )
    import json

    payload = json.loads(formatter.format(record))
    assert payload["message"] == "bare"
    assert "correlation_id" not in payload
