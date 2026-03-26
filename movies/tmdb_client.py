import asyncio
from logging_config import get_logger
import os
import random
import time
from typing import Optional

import httpx


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


TMDB_API_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"

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
    # Class-level semaphore and circuit breaker are intentional: a single async
    # worker shares one event loop, so all TMDbHelper instances must respect the
    # same rate limit and circuit state.  Multi-worker deployments (e.g.
    # gunicorn --workers N) get independent copies per process — coordinate
    # externally if TMDb rate limits are hit across workers.
    _rate_semaphore = asyncio.Semaphore(30)
    _circuit_breaker = _CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_tmdb_api_key()
        self.base_url = TMDB_API_BASE_URL
        self.image_base_url = TMDB_IMAGE_BASE_URL
        self._max_retries = 3
        # Create reusable client with optimized timeouts
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30
            )
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
            raise httpx.RequestError(
                f"TMDb circuit breaker open — request to {endpoint} rejected"
            )

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
                        wait, attempt + 1, self._max_retries,
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
                    url, elapsed_time, response.status_code,
                )
                await self._circuit_breaker.record_success()
                return response.json()

            except httpx.RequestError as e:
                elapsed_time = time.time() - start_time
                await self._circuit_breaker.record_failure()
                if attempt < self._max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "TMDb request error (attempt %d/%d): %s. Retrying in %ds",
                        attempt + 1, self._max_retries, e, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                logger.error(
                    "TMDb request failed after %d attempts: %s; Time elapsed: %.2fs",
                    self._max_retries + 1, e, elapsed_time,
                )
                raise
            except httpx.HTTPStatusError as e:
                elapsed_time = time.time() - start_time
                # 4xx client errors (except 429) are not TMDb failures
                if e.response.status_code >= 500:
                    await self._circuit_breaker.record_failure()
                logger.error(
                    "HTTP error from %s: %s; Time elapsed: %.2fs", url, e, elapsed_time,
                )
                raise
            except Exception as e:
                elapsed_time = time.time() - start_time
                await self._circuit_breaker.record_failure()
                logger.error(
                    "Unexpected error from %s: %s; Time elapsed: %.2fs", url, e, elapsed_time,
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
    # Parsers: extract structured data from the combined response
    # ------------------------------------------------------------------

    def parse_watch_providers(self, data, region="US"):
        """Extract watch providers from a combined response."""
        wp_data = data.get("watch/providers", {})
        region_data = wp_data.get("results", {}).get(region, {})
        if not region_data:
            return None

        providers = {}

        for category, key in [("stream", "flatrate"), ("rent", "rent"), ("buy", "buy"), ("ads", "ads")]:
            if key in region_data:
                providers[category] = [
                    {
                        "provider_name": p.get("provider_name"),
                        "logo_path": (
                            f"{self.image_base_url}w92{p.get('logo_path')}"
                            if p.get("logo_path") else None
                        ),
                    }
                    for p in region_data[key][:4]
                ]

        if "link" in region_data:
            providers["justwatch_link"] = region_data["link"]

        return providers if providers else None

    def parse_age_rating(self, data):
        """Extract US age rating from a combined response."""
        rd_data = data.get("release_dates", {})
        # Prefer US certification
        for country in rd_data.get("results", []):
            if country.get("iso_3166_1") == "US":
                for release in country.get("release_dates", []):
                    cert = release.get("certification", "").strip()
                    if cert:
                        return cert
        # Fallback to first available
        for country in rd_data.get("results", []):
            for release in country.get("release_dates", []):
                cert = release.get("certification", "").strip()
                if cert:
                    return cert
        return "Not Rated"

    def parse_cast(self, data, limit=10):
        """Extract top cast members from a combined response."""
        credits = data.get("credits", {})
        return [
            {
                "name": m["name"],
                "image_url": (
                    f"{self.image_base_url}w185{m['profile_path']}"
                    if m.get("profile_path") else None
                ),
                "character": m.get("character", "N/A"),
            }
            for m in credits.get("cast", [])[:limit]
        ]

    def parse_directors(self, data):
        """Extract director names from a combined response."""
        credits = data.get("credits", {})
        return [
            crew["name"]
            for crew in credits.get("crew", [])
            if crew.get("job") == "Director"
        ]

    def parse_key_crew(self, data):
        """Extract notable crew: writers, composer, cinematographer."""
        credits = data.get("credits", {})
        crew_list = credits.get("crew", [])

        writers = []
        composer = None
        cinematographer = None
        for member in crew_list:
            job = member.get("job", "")
            if job in ("Screenplay", "Writer") and len(writers) < 3:
                writers.append(member["name"])
            elif job == "Original Music Composer" and not composer:
                composer = member["name"]
            elif job == "Director of Photography" and not cinematographer:
                cinematographer = member["name"]

        result = {}
        if writers:
            result["writers"] = writers
        if composer:
            result["composer"] = composer
        if cinematographer:
            result["cinematographer"] = cinematographer
        return result

    def parse_trailer(self, data):
        """Extract YouTube trailer URL from a combined response."""
        for video in data.get("videos", {}).get("results", []):
            if video.get("site") == "YouTube" and video.get("type") == "Trailer":
                return f"https://www.youtube.com/watch?v={video['key']}"
        return None

    def parse_images(self, data, limit=1):
        """Extract poster and backdrop URLs from a combined response."""
        images_data = data.get("images", {})
        posters = images_data.get("posters", [])[:limit]
        backdrops = images_data.get("backdrops", [])[:limit]
        return {
            "posters": [
                f"{self.image_base_url}original{img['file_path']}"
                for img in posters if "file_path" in img
            ],
            "backdrops": [
                f"{self.image_base_url}original{img['file_path']}"
                for img in backdrops if "file_path" in img
            ],
        }

    def parse_keywords(self, data):
        """Extract keyword names from a combined response."""
        return [
            kw["name"]
            for kw in data.get("keywords", {}).get("keywords", [])
        ]

    def parse_recommendations(self, data, limit=10):
        """Extract recommended movies from a combined response."""
        return [
            {
                "tmdb_id": m["id"],
                "title": m.get("title", ""),
                "year": m.get("release_date", "")[:4] if m.get("release_date") else "",
                "poster_url": (
                    f"{self.image_base_url}w342{m['poster_path']}"
                    if m.get("poster_path") else None
                ),
                "vote_average": m.get("vote_average", 0),
            }
            for m in data.get("recommendations", {}).get("results", [])[:limit]
        ]

    def parse_external_ids(self, data):
        """Extract external IDs (IMDb, social) from a combined response."""
        ext = data.get("external_ids", {})
        result = {}
        if ext.get("imdb_id"):
            result["imdb_url"] = f"https://www.imdb.com/title/{ext['imdb_id']}/"
        if ext.get("wikidata_id"):
            result["wikidata_url"] = f"https://www.wikidata.org/wiki/{ext['wikidata_id']}"
        if ext.get("facebook_id"):
            result["facebook_url"] = f"https://www.facebook.com/{ext['facebook_id']}"
        if ext.get("instagram_id"):
            result["instagram_url"] = f"https://www.instagram.com/{ext['instagram_id']}"
        if ext.get("twitter_id"):
            result["twitter_url"] = f"https://x.com/{ext['twitter_id']}"
        return result

    def parse_collection(self, data):
        """Extract collection/franchise info from the movie details."""
        coll = data.get("belongs_to_collection")
        if not coll:
            return None
        return {
            "id": coll["id"],
            "name": coll["name"],
            "poster_url": (
                f"{self.image_base_url}w185{coll['poster_path']}"
                if coll.get("poster_path") else None
            ),
        }

    # ------------------------------------------------------------------
    # Individual-endpoint methods — still used by movie_service.py,
    # movies/movie.py, and scripts/update_languages_from_tmdb.py.
    # ------------------------------------------------------------------

    async def get_images_by_tmdb_id(self, tmdb_id, limit=1):
        """Fetch limited number of images for a TMDB ID."""
        start_time = time.time()
        try:
            data = await self._get(f"movie/{tmdb_id}/images")
            # Limit the number of posters and backdrops to fetch
            posters = data.get("posters", [])[:limit]
            backdrops = data.get("backdrops", [])[:limit]

            images = {
                "posters": [self.image_base_url + "original" + img["file_path"] for img in posters if
                            "file_path" in img],
                "backdrops": [self.image_base_url + "original" + img["file_path"] for img in backdrops if
                              "file_path" in img],
            }

            logger.debug(
                "Found %d poster(s) and %d backdrop(s) for TMDB ID: %s",
                len(images['posters']),
                len(images['backdrops']),
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
        if hasattr(self, '_client'):
            await self._client.aclose()
