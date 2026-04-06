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
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max

        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._half_open_count = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Return current state (read-only, no side effects)."""
        return self._state

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

    async def record_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED

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


class TMDbHelper:
    # Semaphore shared across all instances to respect TMDb rate limits (~40 req/s)
    _rate_semaphore = asyncio.Semaphore(30)
    # Circuit breaker shared across all instances
    _circuit_breaker = _CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)

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

    async def _get(self, endpoint, params=None):
        url = f"{self.base_url}/{endpoint}"
        headers, params = self._build_request_options(params)

        # Circuit breaker check — fail fast when TMDb is known to be down
        if not await self._circuit_breaker.allow_request():
            logger.warning("TMDb circuit breaker OPEN — rejecting request to %s", endpoint)
            raise httpx.RequestError(f"TMDb circuit breaker open — request to {endpoint} rejected")

        for attempt in range(self._max_retries + 1):
            start_time = time.time()
            try:
                async with self._rate_semaphore:
                    response = await self._client.get(url, params=params, headers=headers)

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 1))
                    wait = min(retry_after, 10)
                    logger.warning(
                        "TMDb rate limited (429). Retry-After: %.1fs (attempt %d/%d)",
                        wait,
                        attempt + 1,
                        self._max_retries,
                    )
                    await self._circuit_breaker.record_failure()
                    if attempt < self._max_retries:
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
                await self._circuit_breaker.record_success()
                return response.json()

            except httpx.RequestError as e:
                elapsed_time = time.time() - start_time
                await self._circuit_breaker.record_failure()
                if attempt < self._max_retries:
                    backoff = 2**attempt
                    logger.warning(
                        "TMDb request error (attempt %d/%d): %s. Retrying in %ds",
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
            data = await self._get(f"movie/{tmdb_id}/images")
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
        data = await self._get("find/" + tconst, {"external_source": "imdb_id"})
        return data["movie_results"][0]["id"] if data["movie_results"] else None

    async def get_movie_info_by_tmdb_id(self, tmdb_id):
        return await self._get(f"movie/{tmdb_id}")

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
