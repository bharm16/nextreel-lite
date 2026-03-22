"""Database-related configuration."""

import os


# Determined once at import time.
_flask_env = os.getenv("FLASK_ENV", "development")


class DatabaseConfig:
    """Database connection and pool settings."""

    # Pool sizes
    POOL_MIN_SIZE = 10
    POOL_MAX_SIZE = 30

    @staticmethod
    def get_db_config():
        """Get database configuration based on environment."""
        if _flask_env == "development":
            return {
                "host": os.getenv("DB_HOST", "127.0.0.1"),
                "user": os.getenv("DB_USER", "root"),
                "password": os.getenv("DB_PASSWORD", ""),
                "database": os.getenv("DB_NAME", "imdb"),
                "port": int(os.getenv("DB_PORT", 3306)),
            }
        else:
            return {
                "host": os.getenv("PROD_DB_HOST", os.getenv("DB_HOST", "127.0.0.1")),
                "user": os.getenv("PROD_DB_USER", os.getenv("DB_USER", "root")),
                "password": os.getenv("PROD_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
                "database": os.getenv("PROD_DB_NAME", os.getenv("DB_NAME", "imdb")),
                "port": int(os.getenv("PROD_DB_PORT", os.getenv("DB_PORT", 3306))),
            }

    @staticmethod
    def get_ssl_cert_path():
        return os.getenv("SSL_CERT_PATH", None)

    @staticmethod
    def use_ssl():
        return _flask_env != "development"
