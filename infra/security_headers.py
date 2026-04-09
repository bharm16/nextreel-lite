"""HTTP security response headers.

Extracted from ``session.security`` — these headers are independent of
session management and change when browser security policy evolves.
"""

from config.env import get_environment
from logging_config import get_logger

logger = get_logger(__name__)


# Precomputed header dicts — built once at import time so the per-request
# path only does a dict ``update`` instead of N individual key assignments.
# X-XSS-Protection intentionally omitted — deprecated in modern browsers
# and can introduce vulnerabilities in older ones.
_BASELINE_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
}

_PROD_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net https://kit.fontawesome.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' https://image.tmdb.org data:; "
        "font-src 'self' https://ka-f.fontawesome.com; "
        "connect-src 'self' https://ka-f.fontawesome.com;"
    ),
}


async def add_security_headers(response):
    """Add security headers to every response.

    Baseline headers (X-Frame-Options, X-Content-Type-Options, etc.) are
    applied in all environments.  HSTS and CSP are production-only since
    HSTS pins HTTPS on the domain and CSP may block dev tooling.
    """
    response.headers.update(_BASELINE_HEADERS)
    if get_environment() == "production":
        response.headers.update(_PROD_HEADERS)
    return response
