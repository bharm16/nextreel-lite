import os
from dotenv import load_dotenv


def setup_local_environment():
    """
    Dynamically configure environment variables for local testing or production.
    """
    # Load environment variables from .env file
    load_dotenv(dotenv_path=".env", override=True)

    # Default environment setup
    os.environ.setdefault("FLASK_ENV", "development")
    os.environ.setdefault("SESSION_TYPE", "redis")
    os.environ.setdefault("REDIS_HOST", "localhost")
    os.environ.setdefault("REDIS_PORT", "6379")
    os.environ.setdefault("REDIS_PASSWORD", "")

    # Configure MySQL settings based on FLASK_ENV
    if os.getenv("FLASK_ENV") == "production":
        # Production database settings
        os.environ.setdefault("MYSQL_HOST", "prod-db-hostname")  # Replace with production DB host
        os.environ.setdefault("MYSQL_USER", "prod_user")  # Replace with production DB username
        os.environ.setdefault("MYSQL_PASSWORD", "prod_password")  # Replace with production DB password
        os.environ.setdefault("MYSQL_DB", "prod_database")  # Replace with production database name
        os.environ.setdefault("USER_DB_HOST", "prod-db-hostname")  # Replace with production user DB host
        os.environ.setdefault("USER_DB_USER", "prod_user")  # Replace with production user DB username
        os.environ.setdefault("USER_DB_PASSWORD", "prod_password")  # Replace with production user DB password
        os.environ.setdefault("USER_DB_NAME", "prod_user_database")  # Replace with production user database name
    else:  # Local or testing environment
        # Local settings for Movies database
        os.environ.setdefault("MYSQL_HOST", "localhost")
        os.environ.setdefault("MYSQL_USER", "root")
        os.environ.setdefault("MYSQL_PASSWORD", "caching_sha2_password")
        os.environ.setdefault("MYSQL_DB", "imdb")

        # Local settings for UserAccounts database
        os.environ.setdefault("USER_DB_HOST", "localhost")
        os.environ.setdefault("USER_DB_USER", "root")
        os.environ.setdefault("USER_DB_PASSWORD", "caching_sha2_password")
        os.environ.setdefault("USER_DB_NAME", "UserAccounts")

    # Output for debugging
    print("Environment configured for:", os.getenv("FLASK_ENV"))
    print("Movies Database Host:", os.getenv("MYSQL_HOST"))
    print("Movies Database User:", os.getenv("MYSQL_USER"))
    print("Movies Database Name:", os.getenv("MYSQL_DB"))
    print("UserAccounts Database Host:", os.getenv("USER_DB_HOST"))
    print("UserAccounts Database User:", os.getenv("USER_DB_USER"))
    print("UserAccounts Database Name:", os.getenv("USER_DB_NAME"))


if __name__ == "__main__":
    setup_local_environment()