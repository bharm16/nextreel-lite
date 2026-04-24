import os
import time
from typing import Optional, Dict, Any
from logging_config import get_logger

logger = get_logger(__name__)


class SecretsManager:
    """
    Centralized secrets management with validation and secure defaults.
    Supports multiple secret sources: environment variables, secret managers, etc.
    """

    # Required secrets that must be present for the app to function
    REQUIRED_SECRETS = {
        "TMDB_API_KEY": "TMDb API key for movie data",
        "FLASK_SECRET_KEY": "Flask session secret key",
    }

    # Optional secrets with defaults
    OPTIONAL_SECRETS = {
        "REDIS_PASSWORD": None,
        "SSL_CERT_PATH": None,
    }

    _CACHE_TTL = 300  # 5 minutes — allows runtime rotation without restart

    def __init__(self):
        self._secrets_cache: Dict[str, Dict[str, Any]] = {}  # key -> {"value": ..., "ts": ...}
        self._validated = False

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve a secret value with TTL-based caching."""
        # Check cache (with TTL)
        entry = self._secrets_cache.get(key)
        if entry and (time.time() - entry["ts"]) < self._CACHE_TTL:
            return entry["value"]

        # Try environment variables first
        value = os.environ.get(key)

        # If not in environment, try other sources (extend as needed)
        if not value:
            value = self._get_from_secret_manager(key)

        # Validate required secrets
        if not value and key in self.REQUIRED_SECRETS:
            if not default:
                raise RuntimeError(
                    f"Required secret '{key}' not found. "
                    f"Description: {self.REQUIRED_SECRETS[key]}. "
                    f"Please set the {key} environment variable."
                )
            value = default

        # Use default for optional secrets
        if not value and key in self.OPTIONAL_SECRETS:
            value = default or self.OPTIONAL_SECRETS[key]

        # Cache the value with timestamp
        if value:
            self._secrets_cache[key] = {"value": value, "ts": time.time()}

        return value

    def _get_from_secret_manager(self, key: str) -> Optional[str]:
        """
        Retrieve secret from external secret manager.
        Implement this based on your infrastructure (AWS Secrets Manager, HashiCorp Vault, etc.)
        """
        return None

    def validate_all_secrets(self) -> bool:
        """
        Validate that all required secrets are present.
        Should be called during application startup.
        """
        missing_secrets = []

        for key, description in self.REQUIRED_SECRETS.items():
            try:
                value = self.get_secret(key)
                if not value:
                    missing_secrets.append(f"{key}: {description}")
                else:
                    logger.info("Secret '%s' validated successfully (present and non-empty)", key)
            except RuntimeError:
                missing_secrets.append(f"{key}: {description}")

        if missing_secrets:
            logger.error("Missing required secrets: %s", ", ".join(missing_secrets))
            return False

        self._validated = True
        logger.info("All required secrets validated successfully")
        return True

    def is_validated(self) -> bool:
        """Check if secrets have been validated."""
        return self._validated

    def clear_cache(self):
        """Clear the secrets cache (useful for key rotation)."""
        self._secrets_cache.clear()
        logger.info("Secrets cache cleared")


# Singleton instance
secrets_manager = SecretsManager()
