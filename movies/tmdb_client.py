import asyncio
from logging_config import get_logger
import os
import random
import time
from typing import Optional

import httpx

from movies.tmdb_parser import TMDbResponseParser


def get_tmdb_api_key() -> str:
    """Retrieve the TMDb API key from secure secrets manager.

    Fetching the key on demand enables key rotation without changing code or
    redeploying the application.
    """
    from infra.secrets import secrets_manager

    api_key = secrets_manager.get_secret("TMDB_API_KEY")
    if not api_key:
        raise RuntimeError("TMDB_API_KEY not configured. Please set the environment variable.")
    return api_key


from movies.tmdb_parser import TMDB_IMAGE_BASE_URL

TMDB_API_BASE_URL = "https://api.themoviedb.org/3"

logger = get_logger(__name__)


class _CircuitBreaker:
    """Lightweight circuit breaker for external API calls.

    States:
    - CLOSED: requests flow normally, failures are counted.
    - OPEN: requests are rejected immediately for ``recovery_timeout`` seconds.
    - HALF_OPEN: a single probe request is allowed through; success closes,
      failure re-opens.

    All state mutations are protected by an asyncio.Lock to prevent
    concurrent coroutines from creating race conditions.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max: int = 1,
        latency_threshold_seconds: float | None = None,
        latency_ewma_alpha: float = 0.2,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max
        self.latency_threshold_seconds = latency_threshold_seconds
        self.latency_ewma_alpha = latency_ewma_alpha

        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._half_open_count = 0
        self._latency_ewma: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Return current state (read-only, no side effects)."""
        return self._state

    @property
    def latency_ewma_seconds(self) -> float | None:
        return self._latency_ewma

    async def attempt_recovery(self) -> None:
        """Transition OPEN → HALF_OPEN if the recovery timeout has elapsed."""
        async with self._lock:
            if self._state == self.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = self.HALF_OPEN
                    self._half_open_count = 0

    async def allow_request(self) -> bool:
        await self.attempt_recovery()
        async with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.HALF_OPEN:
                if self._half_open_count < self.half_open_max:
                    self._half_open_count += 1
                    return True
                return False
            return False  # OPEN

    async def record_success(self, duration_seconds: float | None = None) -> None:
        async with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED
            if duration_seconds is not None:
                if self._latency_ewma is None:
                    self._latency_ewma = duration_seconds
                else:
                    alpha = self.latency_ewma_alpha
                    self._latency_ewma = (
                        alpha * duration_seconds + (1 - alpha) * self._latency_ewma
                    )
                if (
                    self.latency_threshold_seconds is not None
                    and self._latency_ewma > self.latency_threshold_seconds
                ):
                    self._state = self.OPEN
                    self._last_failure_time = time.time()
                    logger.warning(
                        "TMDb circuit breaker OPEN (latency EWMA %.2fs > %.2fs threshold)",
                        self._latency_ewma,
                        self.latency_threshold_seconds,
                    )
                    # Reset EWMA after tripping so recovery can measure fresh.
                    self._latency_ewma = None

    async def record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
                logger.warning(
                    "TMDb circuit breaker OPEN after %d consecutive failures",
                    self._failure_count,
                )


# Semaphore shared across all instances. TMDb's documented limit is
# ~40-50 req/s *sustained*; concurrency can go much higher when latency
# is low. Default 200 gives 10x headroom over the old 50 while still
# bounding fan-out during burst enrichment. Override with
# TMDB_RATE_SEMAPHORE env var.
def _resolve_rate_semaphore_size() -> int:
    raw = os.getenv("TMDB_RATE_SEMAPHORE", "200")
    try:
        value = int(raw)
    except ValueError:
        return 200
    return max(1, value)


_rate_semaphore = asyncio.Semaphore(_resolve_rate_semaphore_size())


def _build_circuit_breaker() -> _CircuitBreaker:
    raw = os.getenv("TMDB_LATENCY_BREAKER_SECONDS")
    threshold: float | None = None
    if raw:
        try:
            value = float(raw)
            if value > 0:
                threshold = value
        except ValueError:
            logger.warning(
                "Invalid TMDB_LATENCY_BREAKER_SECONDS=%r, ignoring", raw
            )
    return _CircuitBreaker(
        failure_threshold=5,
        recovery_timeout=30.0,
        latency_threshold_seconds=threshold,
    )


class TMDbHelper:
    _rate_semaphore = _rate_semaphore
    # Circuit breaker shared across all instances
    _circuit_breaker = _build_circuit_breaker()

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_tmdb_api_key()
        self.base_url = TMDB_API_BASE_URL
        self.image_base_url = TMDB_IMAGE_BASE_URL
        self._max_retries = 3
        self._response_parser: TMDbResponseParser | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            limits=httpx.Limits(
                max_keepalive_connections=20, max_connections=50, keepalive_expiry=30
            ),
        )

    def _uses_bearer_auth(self) -> bool:
        """Treat JWT-like tokens as TMDb v4 read access tokens.

        TMDb v3 API keys are opaque strings typically passed as the ``api_key``
        query parameter. TMDb v4 read access tokens are JWT-like bearer tokens.
        Supporting both formats preserves compatibility with existing
        ``TMDB_API_KEY`` values in local and deployed environments.
        """
        token = self.api_key.strip()
        return token.count(".") == 2

    def _build_request_options(self, params=None):
        request_params = dict(params or {})
        headers = {}

        if self._uses_bearer_auth():
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            request_params["api_key"] = self.api_key

        return headers, request_params

    def _record_tmdb_metrics(self, logical_endpoint, status_code, duration_seconds):
        """Best-effort Prometheus emission. Failures never break the caller.

        ``status_code`` is bucketed via ``bucket_http_status`` so non-429
        HTTP codes collapse into ``2xx`` / ``3xx`` / ``4xx`` / ``5xx`` and
        sentinel strings (``circuit_open``, ``transport_error``, ``error``)
        pass through. This keeps the ``status_code`` label bounded to ~8
        values regardless of what TMDb or httpx surface.
        """
        try:
            from infra.metrics import (
                bucket_http_status,
                tmdb_api_calls_total,
                tmdb_api_duration_seconds,
            )

            tmdb_api_calls_total.labels(
                endpoint=logical_endpoint,
                status_code=bucket_http_status(status_code),
            ).inc()
            tmdb_api_duration_seconds.labels(endpoint=logical_endpoint).observe(
                duration_seconds
            )
        except Exception:  # pragma: no cover - metrics must never break requests
            pass

    def _record_tmdb_rate_limit(self, response):
        try:
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is None:
                return
            from infra.metrics import tmdb_rate_limit_remaining

            tmdb_rate_limit_remaining.set(float(remaining))
        except Exception:  # pragma: no cover - best-effort
            pass

    async def _get(self, endpoint, params=None, *, metric_endpoint=None):
        """Fetch a TMDb endpoint.

        ``metric_endpoint`` is the stable low-cardinality label used for
        Prometheus metrics (e.g. ``movie_full``, ``movie_images``,
        ``find_by_imdb``). It MUST NOT include dynamic IDs, URLs, or SQL.
        When absent, a fallback label ``unknown`` is used.
        """
        logical_endpoint = metric_endpoint or "unknown"
        url = f"{self.base_url}/{endpoint}"
        headers, params = self._build_request_options(params)

        # Circuit breaker check — fail fast when TMDb is known to be down
        if not await self._circuit_breaker.allow_request():
            logger.warning("TMDb circuit breaker OPEN — rejecting request to %s", endpoint)
            self._record_tmdb_metrics(logical_endpoint, "circuit_open", 0.0)
            raise httpx.RequestError(f"TMDb circuit breaker open — request to {endpoint} rejected")

        for attempt in range(self._max_retries + 1):
            start_time = time.time()
            metric_recorded = False
            try:
                async with self._rate_semaphore:
                    response = await self._client.get(url, params=params, headers=headers)

                self._record_tmdb_rate_limit(response)

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 1))
                    # Jitter (0-1s) prevents synchronized thundering-herd retries
                    # when many concurrent coroutines receive the same
                    # Retry-After value from TMDb and would otherwise wake in
                    # lockstep and re-trip the circuit breaker.
                    wait = min(retry_after, 10) + random.uniform(0, 1.0)
                    logger.warning(
                        "TMDb rate limited (429). Retry-After: %.1fs (attempt %d/%d)",
                        wait,
                        attempt + 1,
                        self._max_retries,
                    )
                    await self._circuit_breaker.record_failure()
                    self._record_tmdb_metrics(
                        logical_endpoint, 429, time.time() - start_time
                    )
                    metric_recorded = True
                    if attempt < self._max_retries:
                        # Sleep OUTSIDE the rate semaphore (we already exited
                        # the `async with` above) so other callers aren't
                        # starved while we wait out the 429.
                        await asyncio.sleep(wait)
                        continue
                    response.raise_for_status()

                response.raise_for_status()

                elapsed_time = time.time() - start_time
                logger.debug(
                    "Received response from %s in %.2f seconds. Status code: %s",
                    url,
                    elapsed_time,
                    response.status_code,
                )
                await self._circuit_breaker.record_success(duration_seconds=elapsed_time)
                self._record_tmdb_metrics(
                    logical_endpoint, response.status_code, elapsed_time
                )
                return response.json()

            except httpx.RequestError as e:
                elapsed_time = time.time() - start_time
                await self._circuit_breaker.record_failure()
                if not metric_recorded:
                    self._record_tmdb_metrics(
                        logical_endpoint, "transport_error", elapsed_time
                    )
                    metric_recorded = True
                if attempt < self._max_retries:
                    # Exponential backoff with jitter — prevents synchronized
                    # retry storms on shared transport failures (DNS blip,
                    # connection pool exhaustion, etc.).
                    backoff = (2**attempt) + random.uniform(0, 1.0)
                    logger.warning(
                        "TMDb request error (attempt %d/%d): %s. Retrying in %.2fs",
                        attempt + 1,
                        self._max_retries,
                        e,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                logger.error(
                    "TMDb request failed after %d attempts: %s; Time elapsed: %.2fs",
                    self._max_retries + 1,
                    e,
                    elapsed_time,
                )
                raise
            except httpx.HTTPStatusError as e:
                elapsed_time = time.time() - start_time
                # 4xx client errors (except 429) are not TMDb failures
                if e.response.status_code >= 500:
                    await self._circuit_breaker.record_failure()
                if not metric_recorded:
                    self._record_tmdb_metrics(
                        logical_endpoint, e.response.status_code, elapsed_time
                    )
                    metric_recorded = True
                logger.error(
                    "HTTP error from %s: %s; Time elapsed: %.2fs",
                    url,
                    e,
                    elapsed_time,
                )
                raise
            except Exception as e:
                elapsed_time = time.time() - start_time
                await self._circuit_breaker.record_failure()
                if not metric_recorded:
                    self._record_tmdb_metrics(
                        logical_endpoint, "error", elapsed_time
                    )
                    metric_recorded = True
                logger.error(
                    "Unexpected error from %s: %s; Time elapsed: %.2fs",
                    url,
                    e,
                    elapsed_time,
                )
                raise

    async def get_movie_full(self, tmdb_id, region="US"):
        """Fetch movie details plus all sub-resources in a single API call.

        Uses TMDb's ``append_to_response`` to combine what was previously
        6 separate requests into one HTTP round-trip.
        """
        data = await self._get(
            f"movie/{tmdb_id}",
            {
                "append_to_response": (
                    "credits,videos,images,release_dates,"
                    "watch/providers,keywords,recommendations,external_ids"
                ),
                "include_image_language": "en,null",
            },
            metric_endpoint="movie_full",
        )
        return data

    # ------------------------------------------------------------------
    # Parsers: delegated to TMDbResponseParser for SRP.
    # These methods remain for backward compatibility.
    # ------------------------------------------------------------------

    def _parser(self):
        if self._response_parser is None:
            self._response_parser = TMDbResponseParser(self.image_base_url)
        return self._response_parser

    def parse_watch_providers(self, data, region="US"):
        return self._parser().parse_watch_providers(data, region)

    def parse_age_rating(self, data):
        return self._parser().parse_age_rating(data)

    def parse_cast(self, data, limit=10):
        return self._parser().parse_cast(data, limit)

    def parse_directors(self, data):
        return self._parser().parse_directors(data)

    def parse_key_crew(self, data):
        return self._parser().parse_key_crew(data)

    def parse_trailer(self, data):
        return self._parser().parse_trailer(data)

    def parse_images(self, data, limit=1):
        return self._parser().parse_images(data, limit)

    def parse_keywords(self, data):
        return self._parser().parse_keywords(data)

    def parse_recommendations(self, data, limit=10):
        return self._parser().parse_recommendations(data, limit)

    def parse_external_ids(self, data):
        return self._parser().parse_external_ids(data)

    def parse_collection(self, data):
        return self._parser().parse_collection(data)

    # ------------------------------------------------------------------
    # Individual-endpoint methods — still used by movie_service.py,
    # movies/movie.py, and scripts/update_languages_from_tmdb.py.
    # ------------------------------------------------------------------

    async def get_images_by_tmdb_id(self, tmdb_id, limit=1):
        """Fetch limited number of images for a TMDB ID."""
        start_time = time.time()
        try:
            data = await self._get(
                f"movie/{tmdb_id}/images", metric_endpoint="movie_images"
            )
            images = self._parser().parse_images({"images": data}, limit=limit)

            logger.debug(
                "Found %d poster(s) and %d backdrop(s) for TMDB ID: %s",
                len(images["posters"]),
                len(images["backdrops"]),
                tmdb_id,
            )
            return images
        finally:
            elapsed_time = time.time() - start_time
            logger.debug(
                "Completed fetching images for TMDB ID: %s in %.2f seconds",
                tmdb_id,
                elapsed_time,
            )

    async def get_tmdb_id_by_tconst(self, tconst):
        data = await self._get(
            "find/" + tconst,
            {"external_source": "imdb_id"},
            metric_endpoint="find_by_imdb",
        )
        return data["movie_results"][0]["id"] if data["movie_results"] else None

    async def get_movie_info_by_tmdb_id(self, tmdb_id):
        return await self._get(f"movie/{tmdb_id}", metric_endpoint="movie_info")

    def get_full_image_url(self, profile_path, size="original"):
        return f"{self.image_base_url}{size}{profile_path}"

    async def get_backdrop_image_for_home(self, tmdb_id):
        image_data = await self.get_images_by_tmdb_id(tmdb_id)
        backdrops = image_data["backdrops"]
        if backdrops:
            return self.get_full_image_url(backdrops[0])
        return None

    async def get_all_backdrop_images(self, tmdb_id):
        image_data = await self.get_images_by_tmdb_id(tmdb_id)
        backdrops = image_data["backdrops"]
        return [self.get_full_image_url(backdrop) for backdrop in backdrops]

    async def get_backdrop_for_movie(self, tmdb_id):
        all_backdrop_urls = await self.get_all_backdrop_images(tmdb_id)
        if not all_backdrop_urls:
            return None
        return random.choice(all_backdrop_urls)

    async def close(self):
        """Close the HTTP client"""
        if hasattr(self, "_client"):
            await self._client.aclose()
