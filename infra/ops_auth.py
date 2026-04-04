"""Ops endpoint authentication — Bearer token validation.

Extracted from ``routes.py`` so that ops auth policy can evolve
independently of route definitions.
"""

import hmac
import os

from quart import request


def get_ops_auth_token() -> str | None:
    """Read OPS_AUTH_TOKEN lazily so rotation doesn't require a restart."""
    return os.environ.get("OPS_AUTH_TOKEN")


def check_ops_auth() -> bool:
    """Validate bearer token for ops endpoints. Returns True if allowed.

    Fail-closed: when ``OPS_AUTH_TOKEN`` is not configured, access is only
    permitted in development.  Production deployments *must* set the token.
    """
    expected = get_ops_auth_token()
    if not expected:
        from config.env import get_environment

        return get_environment() == "development"
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return hmac.compare_digest(token, expected)
    return False
