#!/usr/bin/env python3
"""Simplified session security for NextReel-Lite.

This module keeps the public integration points stable while removing the
generated, over-engineered Redis shadow-state and crypto-heavy machinery that
was disproportionate to the application's threat model.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Dict, Tuple

from quart import current_app, request, session
from werkzeug.exceptions import Unauthorized

from logging_config import get_logger
from session_keys import (
    FINGERPRINT_COMPONENTS_KEY,
    SESSION_CREATED_KEY,
    SESSION_FINGERPRINT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_ROTATION_COUNT_KEY,
    SESSION_TOKEN_KEY,
)

logger = get_logger(__name__)


class EnhancedSessionSecurity:
    """Session integrity checks with deterministic fingerprinting."""

    def __init__(self, app=None, redis_client=None):
        self.app = None
        self.redis_client = redis_client
        if app is not None:
            self.init_app(app, redis_client=redis_client)

    def init_app(self, app, redis_client=None):
        """Initialize the security hooks on a Quart app."""
        self.app = app
        self.redis_client = redis_client
        self._configure_secure_settings(app)
        app.before_request(self._before_request_handler)
        logger.info("Enhanced session security initialized")

    def _setting(self, key: str, default: int | float) -> int | float:
        if self.app and key in self.app.config:
            return self.app.config[key]
        value = os.getenv(key)
        if value is None:
            return default
        return type(default)(value)

    def _session_idle_timeout(self) -> timedelta:
        minutes = int(self._setting("SESSION_IDLE_TIMEOUT_MINUTES", 15))
        return timedelta(minutes=minutes)

    def _session_max_duration(self) -> timedelta:
        hours = int(self._setting("MAX_SESSION_DURATION_HOURS", 8))
        return timedelta(hours=hours)

    def _rotation_interval(self) -> int:
        return int(self._setting("SESSION_ROTATION_INTERVAL", 10))

    def _configure_secure_settings(self, app):
        """Configure secure cookie and session settings."""
        flask_env = os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production"))

        if flask_env == "production":
            app.config["SESSION_COOKIE_SECURE"] = True
            app.config["SESSION_COOKIE_HTTPONLY"] = True
            app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
            app.config["SESSION_COOKIE_NAME"] = "__Host-session"
        else:
            app.config["SESSION_COOKIE_SECURE"] = False
            app.config["SESSION_COOKIE_HTTPONLY"] = True
            app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
            app.config["SESSION_COOKIE_NAME"] = "session"

        app.config["PERMANENT_SESSION_LIFETIME"] = self._session_max_duration()
        app.config["SESSION_REFRESH_EACH_REQUEST"] = True

    async def _before_request_handler(self):
        """Validate or create a session before each request."""
        skip_paths = ("/static", "/health", "/ready", "/metrics")
        if request.path.startswith(skip_paths):
            return

        if not await self.validate_session():
            await self.create_session()
            return

        await self.update_session_activity()

    def _trusted_proxies(self) -> set[str]:
        return {
            proxy.strip()
            for proxy in os.getenv("TRUSTED_PROXIES", "").split(",")
            if proxy.strip()
        }

    def _get_client_ip(self) -> str:
        """Resolve client IP, trusting forwarded headers only for known proxies."""
        remote_addr = request.remote_addr or ""
        if not remote_addr:
            client = request.scope.get("client")
            if isinstance(client, (list, tuple)) and client:
                remote_addr = client[0]
        if remote_addr and remote_addr in self._trusted_proxies():
            forwarded = request.headers.get("X-Real-IP") or request.headers.get(
                "X-Forwarded-For", ""
            ).split(",")[0].strip()
            return forwarded or remote_addr
        return remote_addr

    def generate_secure_token(self) -> str:
        """Generate a cryptographically secure session token."""
        return secrets.token_urlsafe(32)

    def _fingerprint_components(self) -> Dict[str, str]:
        return {
            "user_agent": request.headers.get("User-Agent", ""),
            "accept": request.headers.get("Accept", ""),
            "accept_language": request.headers.get("Accept-Language", ""),
            "ip": self._get_client_ip(),
        }

    def generate_device_fingerprint(self) -> Tuple[str, Dict[str, str]]:
        """Build a deterministic fingerprint from stable request attributes."""
        components = self._fingerprint_components()
        payload = json.dumps(components, sort_keys=True).encode()
        secret = str(self.app.config.get("SECRET_KEY", "")).encode() if self.app else b""
        if secret:
            fingerprint = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        else:
            fingerprint = hashlib.sha256(payload).hexdigest()
        return fingerprint, components

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    async def validate_session(self) -> bool:
        """Validate the current session state."""
        token = session.get(SESSION_TOKEN_KEY)
        if not token:
            return False

        created = self._parse_timestamp(session.get(SESSION_CREATED_KEY))
        last_activity = self._parse_timestamp(session.get(SESSION_LAST_ACTIVITY_KEY))
        stored_fingerprint = session.get(SESSION_FINGERPRINT_KEY)

        if not created or not last_activity or not stored_fingerprint:
            logger.info("Session missing required security fields")
            return False

        now = datetime.now(timezone.utc)
        if now - created > self._session_max_duration():
            logger.info("Session expired: exceeded maximum duration")
            return False

        if now - last_activity > self._session_idle_timeout():
            logger.info("Session expired: idle timeout")
            return False

        current_fingerprint, components = self.generate_device_fingerprint()
        if not hmac.compare_digest(stored_fingerprint, current_fingerprint):
            logger.warning(
                "Session fingerprint mismatch for token %s from IP %s",
                token[:8],
                components.get("ip", "unknown"),
            )
            return False

        return True

    async def create_session(self) -> Dict[str, Any]:
        """Create a new session with the minimum security state required."""
        session.clear()

        token = self.generate_secure_token()
        fingerprint, components = self.generate_device_fingerprint()
        now = datetime.now(timezone.utc).isoformat()

        session[SESSION_TOKEN_KEY] = token
        session[SESSION_FINGERPRINT_KEY] = fingerprint
        session[SESSION_CREATED_KEY] = now
        session[SESSION_LAST_ACTIVITY_KEY] = now
        session[SESSION_ROTATION_COUNT_KEY] = 0
        session[FINGERPRINT_COMPONENTS_KEY] = components

        logger.info("Created secure session %s", token[:8])

        return {
            "token": token,
            "fingerprint": fingerprint,
            "created": now,
            "last_activity": now,
            "rotation_count": 0,
        }

    async def update_session_activity(self):
        """Update activity timestamps and rotate tokens on a request interval."""
        if SESSION_TOKEN_KEY not in session:
            return

        rotation_count = int(session.get(SESSION_ROTATION_COUNT_KEY, 0)) + 1
        session[SESSION_LAST_ACTIVITY_KEY] = datetime.now(timezone.utc).isoformat()

        if rotation_count >= self._rotation_interval():
            await self.rotate_session_token()
            rotation_count = 0

        session[SESSION_ROTATION_COUNT_KEY] = rotation_count

    async def rotate_session_token(self):
        """Rotate the session token in-place while keeping the session state."""
        old_token = session.get(SESSION_TOKEN_KEY)
        new_token = self.generate_secure_token()
        session[SESSION_TOKEN_KEY] = new_token
        logger.info(
            "Rotated session token %s -> %s",
            old_token[:8] if old_token else "missing",
            new_token[:8],
        )

    async def destroy_session(self):
        """Clear the active session."""
        token = session.get(SESSION_TOKEN_KEY)
        session.clear()
        if token:
            logger.info("Destroyed session %s", token[:8])


def require_secure_session(func):
    """Decorator that rejects requests without a valid session."""

    @wraps(func)
    async def decorated_function(*args, **kwargs):
        manager = current_app.config.get("_session_security")
        if SESSION_TOKEN_KEY not in session:
            raise Unauthorized("No valid session")
        if manager and not await manager.validate_session():
            raise Unauthorized("Invalid session")
        return await func(*args, **kwargs)

    return decorated_function


async def add_security_headers(response):
    """Add the app's security headers to the response."""
    flask_env = os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production"))

    if flask_env == "production":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net https://kit.fontawesome.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' https://image.tmdb.org data:; "
            "font-src 'self' https://ka-f.fontawesome.com; "
            "connect-src 'self' https://ka-f.fontawesome.com;"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    return response
