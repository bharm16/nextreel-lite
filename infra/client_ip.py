"""Trusted-proxy-aware client IP helpers."""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache

from quart import request

from logging_config import get_logger

logger = get_logger(__name__)


def _parse_trusted_networks(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    """Parse TRUSTED_PROXIES into a tuple of ip_network objects.

    Accepts bare IPs (``192.168.1.5``) and CIDR blocks (``10.0.0.0/8``).
    Invalid entries are logged and skipped — a typo must never silently
    turn into "trust nothing" without surfacing the misconfiguration.
    """
    networks: list[ipaddress._BaseNetwork] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning(
                "TRUSTED_PROXIES: ignoring invalid entry %r (must be IP or CIDR)",
                entry,
            )
    return tuple(networks)


@lru_cache(maxsize=1)
def _cached_trusted_networks(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    return _parse_trusted_networks(raw)


def trusted_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    """Return the parsed TRUSTED_PROXIES networks (CIDR-aware)."""
    return _cached_trusted_networks(os.getenv("TRUSTED_PROXIES", ""))


def trusted_proxies() -> set[str]:
    """Legacy accessor returning the literal CIDR/IP strings.

    Kept for callers that want to display configured proxies; the
    membership test in :func:`get_client_ip` uses CIDR-aware matching
    via :func:`trusted_networks`.
    """
    raw = os.getenv("TRUSTED_PROXIES", "")
    return {proxy.strip() for proxy in raw.split(",") if proxy.strip()}


def _is_trusted(remote_addr: str) -> bool:
    if not remote_addr:
        return False
    networks = trusted_networks()
    if not networks:
        return False
    try:
        addr = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def get_client_ip() -> str:
    """Return the best-effort client IP for logging and rate limiting."""
    remote_addr = request.remote_addr or ""
    if not remote_addr:
        client = request.scope.get("client")
        if isinstance(client, (list, tuple)) and client:
            remote_addr = client[0]

    if _is_trusted(remote_addr):
        forwarded = (
            request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        )
        return forwarded or remote_addr or "unknown"

    return remote_addr or "unknown"
