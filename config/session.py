"""Session and cookie configuration."""

import os
import logging

logger = logging.getLogger(__name__)

_flask_env = os.getenv("FLASK_ENV", "development")


class SessionConfig:
    """Session cookie and timeout settings."""

    SESSION_COOKIE_NAME = "session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    @property
    def SESSION_COOKIE_SECURE(self):
        env = os.getenv("FLASK_ENV", "development")
        secure = env != "development"
        if env == "production" and not secure:
            logger.error("WARNING: Secure cookies disabled in production!")
        return secure

    SESSION_COOKIE_DOMAIN = (
        None
        if os.getenv("FLASK_ENV") != "production"
        else os.getenv("COOKIE_DOMAIN", None)
    )

    # Timeout configuration
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))
    SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", 15))
    SESSION_ROTATION_INTERVAL = int(os.getenv("SESSION_ROTATION_INTERVAL", 10))
    MAX_SESSION_DURATION_HOURS = int(os.getenv("MAX_SESSION_DURATION_HOURS", 24))

    # Redis session backend
    SESSION_TYPE = "redis"
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = False
    SESSION_KEY_PREFIX = "session:"
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours in seconds
