import os


def setup_local_environment():
    """Set minimal environment defaults for local development.

    Env-file loading is handled by ``settings.py`` (which picks the right
    file via ``get_environment()``). This helper only marks the process as
    development so the runtime falls back to its built-in localhost defaults
    when `.env` values are absent.
    """
    os.environ.setdefault("NEXTREEL_ENV", "development")
    os.environ.setdefault("FLASK_ENV", "development")  # compat


if __name__ == "__main__":
    setup_local_environment()
