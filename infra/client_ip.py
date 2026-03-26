"""Trusted-proxy-aware client IP helpers."""

from __future__ import annotations

import ipaddress
import os

from quart import request


def trusted_proxies() -> set[str]:
    return {
        proxy.strip()
        for proxy in os.getenv("TRUSTED_PROXIES", "").split(",")
        if proxy.strip()
    }


def _is_valid_ip(value: str) -> bool:
    """Validate that *value* is a well-formed IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(value)
        return True
    except (ValueError, TypeError):
        return False


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
        if forwarded and _is_valid_ip(forwarded):
            return forwarded
        return remote_addr or "unknown"

    return remote_addr or "unknown"
