---
name: tmdb-lookup
description: TMDb API conventions, patterns, and pitfalls specific to this project — prevents accidental violations of auth, caching, and circuit breaker rules
user-invocable: false
---

# TMDb API Conventions — nextreel-lite

This skill provides background knowledge about how this project integrates with The Movie Database (TMDb) API. Follow these rules whenever modifying TMDb-related code.

## Authentication

- **Bearer token only**: API key is sent via `Authorization: Bearer {key}` header, NEVER as a `?api_key=` query parameter.
- Key is fetched on-demand via `get_tmdb_api_key()` in `movies/tmdb_client.py`, which calls `secrets_manager.get_secret("TMDB_API_KEY")`. This allows key rotation without restart.
- The HTTP client (`httpx.AsyncClient`) is configured with `headers={"Authorization": f"Bearer {api_key}"}`.

## Base URLs

- **API**: `https://api.themoviedb.org/3`
- **Images**: `https://image.tmdb.org/t/p/` followed by size and path (e.g., `w500/abc123.jpg`)

## Circuit Breaker (`_CircuitBreaker`)

- Located in `movies/tmdb_client.py`.
- States: CLOSED → OPEN (after `failure_threshold` failures) → HALF_OPEN (after `recovery_timeout` seconds) → CLOSED (on success).
- **All state mutations use `asyncio.Lock`** — never bypass the lock or add synchronous state changes.
- Default: 5 failures to open, 30s recovery, 1 half-open probe.
- Check `allow_request()` before making API calls. Call `record_success()` or `record_failure()` after.

## Credits / Cast

- **One call for credits**: Cast information is derived from the `/movie/{id}/credits` endpoint response — NOT a separate `/movie/{id}/cast` call.
- The credits response includes both `cast` and `crew` arrays. Parse both from the single response.
- Never add a separate API call for cast data.

## Rate Limiting

- TMDb enforces rate limits. The circuit breaker handles this at the application level.
- On 429 responses, record a failure and let the circuit breaker manage backoff.

## Common Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `/movie/{id}` | Movie details |
| `/movie/{id}/credits` | Cast AND crew (single call) |
| `/movie/{id}/videos` | Trailers and clips |
| `/movie/{id}/watch/providers` | Streaming availability |
| `/movie/{id}/images` | Backdrops and posters |

## Error Handling

- Network errors and non-2xx responses go through the circuit breaker.
- Log with `%s`-style formatting: `logger.error("TMDb fetch failed for %s: %s", movie_id, err)`
- Never use f-strings in log calls.

## Caching

- Full movie data is cached in Redis: `cache:movie:full:{tconst}` with 24h TTL.
- Check cache before making API calls.
- Session stores only lightweight refs (`{imdb_id, tmdb_id, title, slug}`), not full movie data.
