"""Route decorator helpers — csrf, rate limiting, and timeout wrappers.

Extracted from routes.py to eliminate duplicated cross-cutting concerns
across route handlers.  Each decorator is independently composable.
"""

from __future__ import annotations

import asyncio
import functools
import hmac
from typing import Callable

from quart import abort, g, request

from infra.rate_limit import check_rate_limit
from logging_config import get_logger

logger = get_logger(__name__)


async def validate_csrf() -> None:
    """Validate CSRF token from form data or X-CSRFToken header.

    Expects the token in ``g.navigation_state.csrf_token``.
    """
    state = getattr(g, "navigation_state", None)
    if state is None:
        abort(503, description="Navigation state unavailable")
    expected = state.csrf_token
    if not expected:
        abort(403, "CSRF token missing from navigation state")

    header_token = request.headers.get("X-CSRFToken")
    if header_token and hmac.compare_digest(header_token, expected):
        return

    form = await request.form
    form_token = form.get("csrf_token", "")
    if form_token and hmac.compare_digest(form_token, expected):
        return

    abort(403, "CSRF token validation failed")


def csrf_required(fn: Callable) -> Callable:
    """Decorator that validates CSRF token before the route runs."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        await validate_csrf()
        return await fn(*args, **kwargs)

    return wrapper


def rate_limited(endpoint_key: str) -> Callable:
    """Decorator factory that applies rate limiting to a route.

    Usage::

        @bp.route("/next_movie", methods=["POST"])
        @rate_limited("next_movie")
        async def next_movie():
            ...
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not await check_rate_limit(endpoint_key):
                return {"error": "rate limited"}, 429
            return await fn(*args, **kwargs)

        return wrapper

    return decorator


def with_timeout(seconds: int = 30) -> Callable:
    """Decorator factory that wraps a route handler in asyncio.wait_for.

    On timeout the handler returns a 504 instead of raising.

    Usage::

        @bp.route("/movie/<tconst>")
        @with_timeout(30)
        async def movie_detail(tconst):
            ...
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                logger.error("Timeout in %s after %ds", fn.__name__, seconds)
                return "Request timed out. Please try again.", 504

        return wrapper

    return decorator
