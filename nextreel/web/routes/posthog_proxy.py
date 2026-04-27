"""Reverse proxy for PostHog browser-SDK ingest endpoints.

Why this exists
---------------
Major ad blockers (uBlock, AdGuard, Brave Shields, Ghostery) ship filter
lists that block PostHog's domain by default. Direct integration silently
loses 20–40% of client-side events depending on the audience.  Routing
the SDK's traffic through the application's own domain at ``/ph/*``
sidesteps the block lists, keeps any cookies first-party, and gives us
a kill-switch we control.

Trade-offs
----------
- We pay the bandwidth cost of forwarding events through our backend.
  At our event volumes this is negligible.
- The browser SDK can no longer talk directly to PostHog if our app is
  down. That's the *correct* trade — analytics availability follows
  application availability, not the other way around.

Security
--------
- Only forwards to the upstream host configured at startup
  (``POSTHOG_HOST``, default ``https://us.i.posthog.com``).
- Path is constrained to a small allow-list of PostHog endpoints so an
  attacker can't use this as an open relay.
- Body is streamed with a hard size cap so we can't be coerced into
  buffering an unbounded payload before forwarding.
- Rate-limited per-client so the proxy can't be used as a free CDN.
- ``Cookie`` and ``Authorization`` headers from the browser are NOT
  forwarded — PostHog SDK calls don't need them and forwarding would
  leak first-party session cookies into a third-party log retention.
- The proxy lives on its own Blueprint so it doesn't inherit the main
  app's context processors (which load the current user on every
  render) — keeps event-capture latency off the DB.
"""

from __future__ import annotations

import httpx
from quart import Blueprint, Response, current_app, request

from infra.events import DEFAULT_POSTHOG_HOST
from infra.route_helpers import rate_limited
from logging_config import get_logger

logger = get_logger(__name__)

posthog_proxy_bp = Blueprint("posthog_proxy", __name__)

# PostHog browser SDK only hits a handful of paths; everything else is
# unexpected and we 404 it rather than acting as an open relay.
_ALLOWED_PREFIXES = (
    "static/",  # SDK bundle
    "array/",   # legacy SDK bundle path
    "e/",       # event capture
    "i/v0/e/",  # event capture (newer)
    "decide/",  # feature-flag decisioning
    "s/",       # session-recording snapshot ingest
    "engage/",  # person-profile updates
    "capture/",
    "batch/",
)

# Allow-list of request headers we forward upstream. Notably absent:
# - ``Cookie`` / ``Set-Cookie``: would leak first-party session cookies.
# - ``Authorization``: SDK doesn't use it; forwarding would leak any
#   bearer tokens an attacker could attach to a forged request.
# - ``Host``: httpx sets this from the upstream URL.
_FORWARD_HEADERS = (
    "content-type",
    "content-encoding",
    "user-agent",
    "accept",
    "accept-encoding",
    "accept-language",
    "x-forwarded-for",
    "x-real-ip",
    "referer",
    "origin",
)

_RESPONSE_HEADERS = (
    "content-type",
    "content-encoding",
    "cache-control",
    "vary",
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-methods",
    "access-control-allow-headers",
)

# Hard cap on the request body we'll buffer/forward. PostHog itself
# rejects payloads above ~1 MiB; we allow a 2 MiB margin for session
# replay snapshots that occasionally hit the high end. Anything larger
# is either malicious or a client bug.
_MAX_BODY_BYTES = 2 * 1024 * 1024


def _get_proxy_client() -> httpx.AsyncClient:
    """Lazily build (and memoize on the app) a long-lived AsyncClient.

    Mirrors the pattern in ``movies/tmdb_client.py`` — a per-request
    client would re-do TCP+TLS to ``us.i.posthog.com`` for every browser
    ping (autocapture, session-replay snapshots, decide polls), which
    becomes the dominant proxy latency cost. Lifecycle teardown closes
    this in ``shutdown_resources``.
    """
    client = getattr(current_app, "posthog_proxy_client", None)
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            limits=httpx.Limits(
                max_keepalive_connections=20, max_connections=50, keepalive_expiry=30
            ),
        )
        current_app.posthog_proxy_client = client
    return client


@posthog_proxy_bp.route(
    "/<path:proxy_path>",
    methods=["GET", "POST", "OPTIONS"],
)
@rate_limited("posthog_proxy")
async def posthog_proxy(proxy_path: str):
    """Forward a PostHog browser-SDK request to the upstream cloud host."""
    config = getattr(current_app, "posthog_config", None) or {}
    if not config.get("enabled"):
        # Returning 204 (not 404) so the SDK fails quietly without
        # generating console errors when PostHog is intentionally off.
        return Response(status=204)

    if not _is_allowed(proxy_path):
        logger.debug("rejecting posthog proxy path: %s", proxy_path)
        return Response("Not found", status=404)

    upstream_host = config.get("upstream_host", DEFAULT_POSTHOG_HOST)
    target_url = f"{upstream_host.rstrip('/')}/{proxy_path}"

    forward_headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _FORWARD_HEADERS
    }

    body: bytes | None = None
    if request.method != "GET":
        # Reject early on declared length when present.
        declared = request.content_length
        if declared is not None and declared > _MAX_BODY_BYTES:
            logger.warning(
                "posthog proxy body too large (declared %d bytes)", declared
            )
            return Response("payload too large", status=413)

        # Stream-bound the actual read so a chunked-encoding request with
        # no Content-Length still can't exhaust memory. ``request.body``
        # is an async iterator yielding raw chunks.
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.body:
            total += len(chunk)
            if total > _MAX_BODY_BYTES:
                logger.warning(
                    "posthog proxy body too large (streamed %d bytes)", total
                )
                return Response("payload too large", status=413)
            chunks.append(chunk)
        body = b"".join(chunks)

    client = _get_proxy_client()
    try:
        upstream_response = await client.request(
            request.method,
            target_url,
            params=request.args,
            content=body,
            headers=forward_headers,
        )
    except httpx.HTTPError as exc:
        logger.warning("posthog proxy upstream error: %s", exc)
        return Response("upstream unavailable", status=502)

    response_headers = {
        name: value
        for name, value in upstream_response.headers.items()
        if name.lower() in _RESPONSE_HEADERS
    }
    return Response(
        upstream_response.content,
        status=upstream_response.status_code,
        headers=response_headers,
    )


def _is_allowed(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES)


__all__ = ["posthog_proxy", "posthog_proxy_bp"]
