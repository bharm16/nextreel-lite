# NextReel-Lite Threat Model

## Protected Assets
- Session integrity for anonymous navigation state
- Database availability and query correctness
- Redis-backed session and cache availability
- Public movie data rendered to end users

## Trust Boundaries
- Browser to Quart app over HTTP/HTTPS
- Quart app to Redis for sessions and cache
- Quart app to MySQL for IMDb data
- Quart app to TMDb for enrichment APIs
- Reverse proxy headers are trusted only when the sender is listed in `TRUSTED_PROXIES`

## Controls Kept
- Secure cookie settings by environment
- Session token creation and rotation
- Idle timeout and maximum session duration
- Deterministic fingerprinting from stable request attributes
- SSL/TLS configuration for database connections
- Health checks, circuit breaker behavior, and slow-query logging for the DB pool
- Redis-backed caching for public movie data without encrypting it

## Controls Removed
- Redis shadow storage for session state outside Quart's own session backend
- PBKDF2/Fernet session encryption
- HMAC/Fernet cache signing and encryption for public movie data
- CPU/process entropy mixing via `psutil`
- Fingerprint similarity scoring and extensive request-header fingerprinting
- Redis-backed security event persistence
- Per-user/IP DB connection accounting and per-user DB query rate limiting

## Rationale
- The app serves public movie data and has no user accounts, payments, or PII.
- The remaining controls protect session integrity and operational stability without creating a second state system or large custom security surface.
- Complexity that could not be tied to a concrete threat was removed in favor of simpler, testable behavior.
