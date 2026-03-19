import os
from dotenv import load_dotenv
from logging_config import get_logger
from secrets_manager import secrets_manager

logger = get_logger(__name__)

flask_env = os.getenv("FLASK_ENV", "development")
# Determine which .env file to load based on FLASK_ENV
# flask_env = os.getenv('FLASK_ENV', 'production')
logger.debug("FLASK_ENV is set to: %s", flask_env)

env_file = ".env.development" if flask_env == "development" else ".env"
load_dotenv(dotenv_path=env_file)
logger.debug("Loaded .env file: %s", env_file)
logger.debug("Database Host from environment: %s", os.getenv("DB_HOST"))


class Config:
    # Common configurations
    @staticmethod
    def get_flask_secret_key():
        """Get Flask secret key from secure source."""
        return secrets_manager.get_secret("FLASK_SECRET_KEY")

    @staticmethod
    def get_tmdb_api_key():
        """Get TMDB API key from secure source."""
        return secrets_manager.get_secret("TMDB_API_KEY")

    # Dynamic properties for backward compatibility
    @property
    def SECRET_KEY(self):
        return self.get_flask_secret_key()

    @property
    def TMDB_API_KEY(self):
        return self.get_tmdb_api_key()

    # Session Security Configuration
    SECRET_KEY = secrets_manager.get_secret("FLASK_SECRET_KEY")

    # Session Cookie Security
    SESSION_COOKIE_NAME = "session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"  # or 'Strict' for higher security

    # Force HTTPS in production
    @property
    def SESSION_COOKIE_SECURE(self):
        """Enable secure cookies in production."""
        env = os.getenv("FLASK_ENV", "development")
        secure = env != "development"
        if env == "production" and not secure:
            logger.error("WARNING: Secure cookies disabled in production!")
        return secure

    # Additional security headers
    SESSION_COOKIE_DOMAIN = (
        None
        if os.getenv("FLASK_ENV") != "production"
        else os.getenv("COOKIE_DOMAIN", None)
    )

    # Session timeouts
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))
    SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", 15))
    SESSION_ROTATION_INTERVAL = int(os.getenv("SESSION_ROTATION_INTERVAL", 10))
    MAX_SESSION_DURATION_HOURS = int(os.getenv("MAX_SESSION_DURATION_HOURS", 24))

    # Redis session configuration
    SESSION_TYPE = "redis"
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = False
    SESSION_KEY_PREFIX = "session:"
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours in seconds

    # Expose production database configuration for scripts that need it
    # Legacy config - kept for backward compatibility but not used
    STACKHERO_DB_CONFIG = {}

    # Dynamically switch database configurations based on FLASK_ENV
    @staticmethod
    def get_db_config():
        """Get database configuration based on environment"""
        if flask_env == "development":
            # Development configuration - local MySQL
            return {
                "host": os.getenv("DB_HOST", "127.0.0.1"),
                "user": os.getenv("DB_USER", "root"),
                "password": os.getenv("DB_PASSWORD", ""),
                "database": os.getenv("DB_NAME", "imdb"),
                "port": int(os.getenv("DB_PORT", 3306)),
            }
        else:
            # Production configuration - use production database variables
            # These can be your cloud provider's database or any production MySQL
            return {
                "host": os.getenv("PROD_DB_HOST", os.getenv("DB_HOST", "127.0.0.1")),
                "user": os.getenv("PROD_DB_USER", os.getenv("DB_USER", "root")),
                "password": os.getenv("PROD_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
                "database": os.getenv("PROD_DB_NAME", os.getenv("DB_NAME", "imdb")),
                "port": int(os.getenv("PROD_DB_PORT", os.getenv("DB_PORT", 3306))),
            }

    # SSL Certificate Path
    @staticmethod
    def get_ssl_cert_path():
        return os.getenv('SSL_CERT_PATH', None)

    # Pool configurations for aiomysql - Optimized for performance
    POOL_MIN_SIZE = 10  # Increased from 5 for better responsiveness
    POOL_MAX_SIZE = 30  # Reduced from 50 for better resource management

    # SSL usage based on environment
    @staticmethod
    def use_ssl():
        return flask_env != "development"


# Re-export DatabaseConnectionPool and pool helpers for backward compatibility.
# The canonical implementation now lives in database/pool.py.
from database.pool import DatabaseConnectionPool, init_pool, get_pool, close_pool
