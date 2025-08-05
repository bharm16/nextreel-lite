"""Utility to populate environment variables for local development.

This helper is executed automatically when the application detects that it is
not running in production.  Values are loaded from a ``.env`` file and sensible
defaults are provided so that the application can start with minimal setup.
The function is intentionally simple; it merely sets ``os.environ`` keys.
"""

import os
from dotenv import load_dotenv


def setup_local_environment():
    """Populate ``os.environ`` with values for development or production."""

    # Load variables from a .env file if present. ``override=True`` ensures that
    # existing environment variables take precedence when running in CI.
    load_dotenv(dotenv_path=".env", override=True)

    # Baseline configuration used by both environments
    os.environ.setdefault("FLASK_ENV", "development")
    os.environ.setdefault("SESSION_TYPE", "redis")
    os.environ.setdefault("REDIS_HOST", "localhost")
    os.environ.setdefault("REDIS_PORT", "6379")
    os.environ.setdefault("REDIS_PASSWORD", "")

    if os.getenv("FLASK_ENV") == "production":
        # Placeholder production settings – real values would be injected during
        # deployment.
        os.environ.setdefault("MYSQL_HOST", "prod-db-hostname")
        os.environ.setdefault("MYSQL_USER", "prod_user")
        os.environ.setdefault("MYSQL_PASSWORD", "prod_password")
        os.environ.setdefault("MYSQL_DB", "prod_database")
        os.environ.setdefault("USER_DB_HOST", "prod-db-hostname")
        os.environ.setdefault("USER_DB_USER", "prod_user")
        os.environ.setdefault("USER_DB_PASSWORD", "prod_password")
        os.environ.setdefault("USER_DB_NAME", "prod_user_database")
    else:
        # Local development defaults for the Movies database
        os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
        os.environ.setdefault("MYSQL_USER", "root")
        os.environ.setdefault("MYSQL_PASSWORD", "caching_sha2_password")
        os.environ.setdefault("MYSQL_DB", "imdb")

        # Local development defaults for the user accounts database
        os.environ.setdefault("USER_DB_HOST", "127.0.0.1")
        os.environ.setdefault("USER_DB_USER", "root")
        os.environ.setdefault("USER_DB_PASSWORD", "caching_sha2_password")
        os.environ.setdefault("USER_DB_NAME", "UserAccounts")

    # Emit values to the console to aid debugging during development.
    print("Environment configured for:", os.getenv("FLASK_ENV"))
    print("Movies Database Host:", os.getenv("MYSQL_HOST"))
    print("Movies Database User:", os.getenv("MYSQL_USER"))
    print("Movies Database Name:", os.getenv("MYSQL_DB"))
    print("UserAccounts Database Host:", os.getenv("USER_DB_HOST"))
    print("UserAccounts Database User:", os.getenv("USER_DB_USER"))
    print("UserAccounts Database Name:", os.getenv("USER_DB_NAME"))


if __name__ == "__main__":
    setup_local_environment()

