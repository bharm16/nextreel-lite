import os
from dotenv import load_dotenv


def setup_local_environment():
    """
    Dynamically configure environment variables for local testing or production.
    """
    # Load environment variables from .env file
    load_dotenv(dotenv_path=".env", override=True)

    os.environ.setdefault("FLASK_ENV", "development")
    os.environ.setdefault("SESSION_TYPE", "redis")
    os.environ.setdefault("REDIS_HOST", "localhost")
    os.environ.setdefault("REDIS_PORT", "6379")
    os.environ.setdefault("REDIS_PASSWORD", os.getenv("REDIS_PASSWORD", ""))

    # Configure MySQL settings based on FLASK_ENV
    if os.getenv("FLASK_ENV") == "production":
        os.environ.setdefault("MYSQL_HOST", os.getenv("MYSQL_HOST", ""))
        os.environ.setdefault("MYSQL_USER", os.getenv("MYSQL_USER", ""))
        os.environ.setdefault("MYSQL_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))
        os.environ.setdefault("MYSQL_DB", os.getenv("MYSQL_DB", ""))
        os.environ.setdefault("USER_DB_HOST", os.getenv("USER_DB_HOST", ""))
        os.environ.setdefault("USER_DB_USER", os.getenv("USER_DB_USER", ""))
        os.environ.setdefault("USER_DB_PASSWORD", os.getenv("USER_DB_PASSWORD", ""))
        os.environ.setdefault("USER_DB_NAME", os.getenv("USER_DB_NAME", ""))
    else:  # Local or testing environment
        os.environ.setdefault("MYSQL_HOST", os.getenv("MYSQL_HOST", "127.0.0.1"))
        os.environ.setdefault("MYSQL_USER", os.getenv("MYSQL_USER", "root"))
        os.environ.setdefault("MYSQL_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))
        os.environ.setdefault("MYSQL_DB", os.getenv("MYSQL_DB", "imdb"))
        os.environ.setdefault("USER_DB_HOST", os.getenv("USER_DB_HOST", "127.0.0.1"))
        os.environ.setdefault("USER_DB_USER", os.getenv("USER_DB_USER", "root"))
        os.environ.setdefault("USER_DB_PASSWORD", os.getenv("USER_DB_PASSWORD", ""))
        os.environ.setdefault("USER_DB_NAME", os.getenv("USER_DB_NAME", "UserAccounts"))


if __name__ == "__main__":
    setup_local_environment()