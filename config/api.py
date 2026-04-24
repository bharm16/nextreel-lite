"""External API configuration (TMDb, secrets)."""

from infra.secrets import secrets_manager


class ApiConfig:
    """API keys and external service configuration."""

    @staticmethod
    def get_flask_secret_key():
        return secrets_manager.get_secret("FLASK_SECRET_KEY")

    @staticmethod
    def get_tmdb_api_key():
        return secrets_manager.get_secret("TMDB_API_KEY")

    @property
    def SECRET_KEY(self):
        """Lazy evaluation — fetched on first access, not at import time."""
        return ApiConfig.get_flask_secret_key()
