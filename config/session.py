"""Session and cookie configuration."""

import os

from config.env import get_environment
from infra.time_utils import env_int

_ONE_DAY_SECONDS = 24 * 60 * 60


class SessionConfig:
    """Session cookie and timeout settings."""

    SESSION_COOKIE_NAME = "session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    @property
    def SESSION_COOKIE_SECURE(self):
        return get_environment() != "development"

    @property
    def SESSION_COOKIE_DOMAIN(self):
        if get_environment() != "production":
            return None
        return os.getenv("COOKIE_DOMAIN", None)

    # Navigation-state lifetime configuration.
    SESSION_IDLE_TIMEOUT_MINUTES = env_int("SESSION_IDLE_TIMEOUT_MINUTES", 15)
    MAX_SESSION_DURATION_HOURS = env_int("MAX_SESSION_DURATION_HOURS", 8)

    # Redis session backend
    SESSION_TYPE = "redis"
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = "session:"
    PERMANENT_SESSION_LIFETIME = _ONE_DAY_SECONDS
