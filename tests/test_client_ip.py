"""Tests for infra.client_ip — trusted proxy detection and client IP extraction."""

import os
from unittest.mock import MagicMock, patch

import pytest

from infra.client_ip import get_client_ip, trusted_proxies


# --- trusted_proxies() ---


def test_trusted_proxies_empty_when_env_unset():
    """No TRUSTED_PROXIES env var yields an empty set."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TRUSTED_PROXIES", None)
        assert trusted_proxies() == set()


def test_trusted_proxies_empty_string():
    """Empty string yields an empty set (no phantom empty-string entry)."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": ""}):
        assert trusted_proxies() == set()


def test_trusted_proxies_single_ip():
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1"}):
        assert trusted_proxies() == {"10.0.0.1"}


def test_trusted_proxies_multiple_ips():
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1,10.0.0.2,172.16.0.1"}):
        assert trusted_proxies() == {"10.0.0.1", "10.0.0.2", "172.16.0.1"}


def test_trusted_proxies_strips_whitespace():
    """Leading/trailing whitespace around each entry is stripped."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": " 10.0.0.1 , 10.0.0.2 "}):
        assert trusted_proxies() == {"10.0.0.1", "10.0.0.2"}


def test_trusted_proxies_ignores_blank_segments():
    """Consecutive commas or trailing commas don't produce empty entries."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1,,10.0.0.2,"}):
        assert trusted_proxies() == {"10.0.0.1", "10.0.0.2"}


# --- get_client_ip() — untrusted remote_addr ---


async def test_get_client_ip_returns_remote_addr_when_not_trusted(app):
    """Direct client (not a trusted proxy) returns its own remote_addr."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1"}):
        mock_request = MagicMock()
        mock_request.remote_addr = "192.168.1.50"
        mock_request.headers = {}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "192.168.1.50"


async def test_get_client_ip_ignores_forwarded_headers_when_not_trusted(app):
    """X-Forwarded-For is ignored when remote_addr is not a trusted proxy."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1"}):
        mock_request = MagicMock()
        mock_request.remote_addr = "192.168.1.50"
        mock_request.headers = {"X-Forwarded-For": "8.8.8.8", "X-Real-IP": "8.8.4.4"}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "192.168.1.50"


# --- get_client_ip() — trusted proxy with forwarding headers ---


async def test_get_client_ip_returns_x_real_ip_when_trusted(app):
    """When remote_addr is a trusted proxy, X-Real-IP is preferred."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1,10.0.0.2"}):
        mock_request = MagicMock()
        mock_request.remote_addr = "10.0.0.1"
        mock_request.headers = {"X-Real-IP": "203.0.113.5"}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "203.0.113.5"


async def test_get_client_ip_returns_x_forwarded_for_first_entry(app):
    """Falls back to first X-Forwarded-For entry when X-Real-IP is absent."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1"}):
        mock_request = MagicMock()
        mock_request.remote_addr = "10.0.0.1"
        mock_request.headers = {"X-Forwarded-For": "203.0.113.5, 10.0.0.1"}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "203.0.113.5"


async def test_get_client_ip_strips_x_forwarded_for_whitespace(app):
    """X-Forwarded-For first entry is stripped of whitespace."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1"}):
        mock_request = MagicMock()
        mock_request.remote_addr = "10.0.0.1"
        mock_request.headers = {"X-Forwarded-For": "  203.0.113.5  , 10.0.0.1"}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "203.0.113.5"


async def test_get_client_ip_x_real_ip_takes_precedence_over_xff(app):
    """X-Real-IP wins when both X-Real-IP and X-Forwarded-For are present."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1"}):
        mock_request = MagicMock()
        mock_request.remote_addr = "10.0.0.1"
        mock_request.headers = {
            "X-Real-IP": "1.2.3.4",
            "X-Forwarded-For": "5.6.7.8",
        }
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "1.2.3.4"


# --- get_client_ip() — no remote_addr / unknown ---


async def test_get_client_ip_returns_unknown_when_no_remote_addr(app):
    """Returns 'unknown' when remote_addr is None and no scope client."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": ""}):
        mock_request = MagicMock()
        mock_request.remote_addr = None
        mock_request.scope = {}
        mock_request.headers = {}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "unknown"


async def test_get_client_ip_returns_unknown_when_remote_addr_empty(app):
    """Returns 'unknown' when remote_addr is empty string and no scope client."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": ""}):
        mock_request = MagicMock()
        mock_request.remote_addr = ""
        mock_request.scope = {}
        mock_request.headers = {}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "unknown"


# --- get_client_ip() — scope client fallback ---


async def test_get_client_ip_falls_back_to_scope_client(app):
    """When remote_addr is falsy, falls back to request.scope['client']."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": ""}):
        mock_request = MagicMock()
        mock_request.remote_addr = None
        mock_request.scope = {"client": ("198.51.100.1", 12345)}
        mock_request.headers = {}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "198.51.100.1"


async def test_get_client_ip_scope_client_list(app):
    """Scope client can be a list (not just a tuple)."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": ""}):
        mock_request = MagicMock()
        mock_request.remote_addr = ""
        mock_request.scope = {"client": ["198.51.100.1", 54321]}
        mock_request.headers = {}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "198.51.100.1"


async def test_get_client_ip_scope_client_empty_list(app):
    """Empty scope client list still returns 'unknown'."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": ""}):
        mock_request = MagicMock()
        mock_request.remote_addr = ""
        mock_request.scope = {"client": []}
        mock_request.headers = {}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "unknown"


# --- get_client_ip() — trusted proxy with no forwarding headers ---


async def test_get_client_ip_trusted_proxy_no_headers_returns_proxy_ip(app):
    """When remote_addr is trusted but no forwarding headers, returns proxy IP."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": "10.0.0.1"}):
        mock_request = MagicMock()
        mock_request.remote_addr = "10.0.0.1"
        mock_request.headers = {}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "10.0.0.1"


# --- get_client_ip() — no trusted proxies configured ---


async def test_get_client_ip_no_proxies_configured(app):
    """Without TRUSTED_PROXIES, always returns remote_addr directly."""
    with patch.dict(os.environ, {"TRUSTED_PROXIES": ""}):
        mock_request = MagicMock()
        mock_request.remote_addr = "192.168.1.1"
        mock_request.headers = {"X-Forwarded-For": "8.8.8.8"}
        with patch("infra.client_ip.request", mock_request):
            assert get_client_ip() == "192.168.1.1"
