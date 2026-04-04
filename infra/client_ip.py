"""Trusted-proxy-aware client IP helpers."""

from __future__ import annotations

import os

from quart import request


def trusted_proxies() -> set[str]:
    return {
        proxy.strip()
        for proxy in os.getenv("TRUSTED_PROXIES", "").split(",")
        if proxy.strip()
    }


def get_client_ip() -> str:
    """Return the best-effort client IP for logging and rate limiting."""
    remote_addr = request.remote_addr or ""
    if not remote_addr:
        client = request.scope.get("client")
        if isinstance(client, (list, tuple)) and client:
            remote_addr = client[0]

    if remote_addr and remote_addr in trusted_proxies():
        forwarded = (
            request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        )
        return forwarded or remote_addr or "unknown"

    return remote_addr or "unknown"
