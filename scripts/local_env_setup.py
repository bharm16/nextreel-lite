import os


def setup_local_environment():
    """Set fallback environment defaults for local development.

    Env-file loading is handled by ``settings.py`` (which picks the right
    file via ``get_environment()``).  This function only fills in missing
    vars with safe localhost defaults so the app can boot without a fully
    populated ``.env.development``.
    """
    os.environ.setdefault("NEXTREEL_ENV", "development")
    os.environ.setdefault("FLASK_ENV", "development")  # compat
    os.environ.setdefault("SESSION_TYPE", "redis")
    os.environ.setdefault("REDIS_HOST", "localhost")
    os.environ.setdefault("REDIS_PORT", "6379")
    os.environ.setdefault("REDIS_PASSWORD", "")

    # Configure MySQL settings based on NEXTREEL_ENV
    if os.getenv("NEXTREEL_ENV", os.getenv("FLASK_ENV")) == "production":
        os.environ.setdefault("MYSQL_HOST", "")
        os.environ.setdefault("MYSQL_USER", "")
        os.environ.setdefault("MYSQL_PASSWORD", "")
        os.environ.setdefault("MYSQL_DB", "")
    else:  # Local or testing environment
        os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
        os.environ.setdefault("MYSQL_USER", "root")
        os.environ.setdefault("MYSQL_PASSWORD", "")
        os.environ.setdefault("MYSQL_DB", "imdb")


if __name__ == "__main__":
    setup_local_environment()