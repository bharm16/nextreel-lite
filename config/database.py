"""Database-related configuration."""

import os

from config.env import get_environment


class DatabaseConfig:
    """Database connection and pool settings."""

    # Pool sizes — authoritative defaults, also read from POOL_MIN_SIZE /
    # POOL_MAX_SIZE env vars in infra/pool.py. Sized for ~6 concurrent
    # `/next_movie` requests per worker (3 acquires each) with headroom
    # for `/movie/<tconst>` (2 acquires) and background jobs.
    POOL_MIN_SIZE = 5
    POOL_MAX_SIZE = 60

    @staticmethod
    def get_db_config():
        """Get database configuration based on environment."""
        if get_environment() == "development":
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
        return get_environment() != "development"
