from unittest.mock import AsyncMock, patch

from movies.tmdb_client import TMDbHelper


def test_get_full_image_url():
    helper = TMDbHelper("key")
    url = helper.get_full_image_url("/path", size="w500")
    assert url == "https://image.tmdb.org/t/p/w500/path"


def test_build_request_options_v3_key_uses_query_param_auth():
    helper = TMDbHelper("1234567890abcdef1234567890abcdef")

    headers, params = helper._build_request_options({"language": "en-US"})

    assert headers == {}
    assert params == {
        "language": "en-US",
        "api_key": "1234567890abcdef1234567890abcdef",
    }


def test_build_request_options_v4_token_uses_bearer_header():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ0bWRiIn0.signature"
    helper = TMDbHelper(token)

    headers, params = helper._build_request_options({"language": "en-US"})

    assert headers == {"Authorization": f"Bearer {token}"}
    assert params == {"language": "en-US"}
    assert "api_key" not in params


def test_build_request_options_preserves_other_params_without_injecting_api_key():
    helper = TMDbHelper("abc.def.ghi")

    headers, params = helper._build_request_options(
        {"language": "en", "append_to_response": "credits"}
    )

    assert headers == {"Authorization": "Bearer abc.def.ghi"}
    assert params == {"language": "en", "append_to_response": "credits"}
    assert "api_key" not in params


async def test_get_backdrop_image_for_home():
    helper = TMDbHelper("key")
    with patch.object(
        helper, "_get", AsyncMock(return_value={"backdrops": [{"file_path": "/b.jpg"}]})
    ):
        url = await helper.get_backdrop_image_for_home(123)
        assert url.endswith("/b.jpg")


async def test_get_backdrop_for_movie():
    helper = TMDbHelper("key")
    helper.get_all_backdrop_images = AsyncMock(return_value=["url1", "url2"])
    with patch("random.choice", lambda x: x[0]):
        url = await helper.get_backdrop_for_movie(123)
        assert url == "url1"


def test_tmdb_semaphore_size_reads_env_var(monkeypatch):
    """The module-level rate semaphore size should come from TMDB_RATE_SEMAPHORE."""
    monkeypatch.setenv("TMDB_RATE_SEMAPHORE", "77")
    import importlib
    import movies.tmdb_client as tmdb_module
    importlib.reload(tmdb_module)
    try:
        assert tmdb_module._rate_semaphore._value == 77
    finally:
        monkeypatch.delenv("TMDB_RATE_SEMAPHORE", raising=False)
        importlib.reload(tmdb_module)


def test_tmdb_semaphore_default_is_50(monkeypatch):
    monkeypatch.delenv("TMDB_RATE_SEMAPHORE", raising=False)
    import importlib
    import movies.tmdb_client as tmdb_module
    importlib.reload(tmdb_module)
    assert tmdb_module._rate_semaphore._value == 50


import time

import pytest

from movies.tmdb_client import _CircuitBreaker, _build_circuit_breaker


class TestCircuitBreakerLatency:
    async def test_record_success_without_duration_keeps_ewma_none(self):
        breaker = _CircuitBreaker(latency_threshold_seconds=1.0)
        for _ in range(5):
            await breaker.record_success()
        assert breaker.latency_ewma_seconds is None
        assert breaker.state == _CircuitBreaker.CLOSED

    async def test_ewma_updates_correctly(self):
        breaker = _CircuitBreaker(latency_ewma_alpha=0.5)
        await breaker.record_success(duration_seconds=1.0)
        await breaker.record_success(duration_seconds=3.0)
        assert breaker.latency_ewma_seconds == pytest.approx(2.0)

    async def test_latency_trip_opens_breaker(self):
        breaker = _CircuitBreaker(latency_threshold_seconds=2.0, latency_ewma_alpha=0.5)
        for _ in range(5):
            await breaker.record_success(duration_seconds=5.0)
        assert breaker.state == _CircuitBreaker.OPEN
        assert await breaker.allow_request() is False

    async def test_no_trip_when_threshold_none(self):
        breaker = _CircuitBreaker(latency_threshold_seconds=None)
        for _ in range(10):
            await breaker.record_success(duration_seconds=30.0)
        assert breaker.state == _CircuitBreaker.CLOSED
        assert await breaker.allow_request() is True

    async def test_recovery_after_latency_trip(self):
        breaker = _CircuitBreaker(
            latency_threshold_seconds=1.0,
            latency_ewma_alpha=1.0,
            recovery_timeout=30.0,
        )
        await breaker.record_success(duration_seconds=5.0)
        assert breaker.state == _CircuitBreaker.OPEN
        breaker._last_failure_time = time.time() - 60
        assert await breaker.allow_request() is True

    def test_invalid_env_var_ignored(self, monkeypatch):
        monkeypatch.setenv("TMDB_LATENCY_BREAKER_SECONDS", "not-a-float")
        breaker = _build_circuit_breaker()
        assert breaker.latency_threshold_seconds is None

    def test_zero_env_var_ignored(self, monkeypatch):
        monkeypatch.setenv("TMDB_LATENCY_BREAKER_SECONDS", "0")
        breaker = _build_circuit_breaker()
        assert breaker.latency_threshold_seconds is None

    def test_negative_env_var_ignored(self, monkeypatch):
        monkeypatch.setenv("TMDB_LATENCY_BREAKER_SECONDS", "-1")
        breaker = _build_circuit_breaker()
        assert breaker.latency_threshold_seconds is None
