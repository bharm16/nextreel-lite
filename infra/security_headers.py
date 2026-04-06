"""HTTP security response headers.

Extracted from ``session.security`` — these headers are independent of
session management and change when browser security policy evolves.
"""

from logging_config import get_logger

logger = get_logger(__name__)


async def add_security_headers(response):
    """Add security headers to every response.

    Baseline headers (X-Frame-Options, X-Content-Type-Options, etc.) are
    applied in all environments.  HSTS and CSP are production-only since
    HSTS pins HTTPS on the domain and CSP may block dev tooling.
    """
    from config.env import get_environment

    # ── Baseline headers — all environments ──────────────────────────
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers[
        "Permissions-Policy"
    ] = "geolocation=(), camera=(), microphone=(), payment=(), usb=()"
    # X-XSS-Protection intentionally omitted — deprecated in modern
    # browsers and can introduce vulnerabilities in older ones.

    # ── Production-only headers ──────────────────────────────────────
    if get_environment() == "production":
        response.headers[
            "Strict-Transport-Security"
        ] = "max-age=31536000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net https://kit.fontawesome.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' https://image.tmdb.org data:; "
            "font-src 'self' https://ka-f.fontawesome.com; "
            "connect-src 'self' https://ka-f.fontawesome.com;"
        )

    return response
