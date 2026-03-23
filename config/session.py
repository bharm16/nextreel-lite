"""Session and cookie configuration."""

import os
from logging_config import get_logger

logger = get_logger(__name__)

_flask_env = os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production"))


class SessionConfig:
    """Session cookie and timeout settings."""

    SESSION_COOKIE_NAME = "session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    @property
    def SESSION_COOKIE_SECURE(self):
        env = os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production"))
        secure = env != "development"
        if env == "production" and not secure:
            logger.error("WARNING: Secure cookies disabled in production!")
        return secure

    SESSION_COOKIE_DOMAIN = (
        None
        if os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV", "production")) != "production"
        else os.getenv("COOKIE_DOMAIN", None)
    )

    # Timeout configuration — these are read-through defaults.
    # EnhancedSessionSecurity owns the effective values at runtime.
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))
    SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", 15))
    SESSION_ROTATION_INTERVAL = int(os.getenv("SESSION_ROTATION_INTERVAL", 10))
    MAX_SESSION_DURATION_HOURS = int(os.getenv("MAX_SESSION_DURATION_HOURS", 8))

    # Redis session backend
    SESSION_TYPE = "redis"
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = "session:"
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours in seconds
