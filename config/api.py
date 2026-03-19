"""External API configuration (TMDb, secrets)."""

from secrets_manager import secrets_manager


class ApiConfig:
    """API keys and external service configuration."""

    @staticmethod
    def get_flask_secret_key():
        return secrets_manager.get_secret("FLASK_SECRET_KEY")

    @staticmethod
    def get_tmdb_api_key():
        return secrets_manager.get_secret("TMDB_API_KEY")

    SECRET_KEY = secrets_manager.get_secret("FLASK_SECRET_KEY")
