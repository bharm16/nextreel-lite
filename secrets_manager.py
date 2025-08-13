import os
import logging
from typing import Optional, Dict, Any
from functools import lru_cache
import json

logger = logging.getLogger(__name__)


class SecretsManager:
    """
    Centralized secrets management with validation and secure defaults.
    Supports multiple secret sources: environment variables, secret managers, etc.
    """
    
    # Required secrets that must be present for the app to function
    REQUIRED_SECRETS = {
        'TMDB_API_KEY': 'TMDb API key for movie data',
        'FLASK_SECRET_KEY': 'Flask session secret key',
    }
    
    # Optional secrets with defaults
    OPTIONAL_SECRETS = {
        'REDIS_PASSWORD': None,
        'SSL_CERT_PATH': None,
    }
    
    def __init__(self):
        self._secrets_cache: Dict[str, Any] = {}
        self._validated = False
        
    @lru_cache(maxsize=32)
    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Retrieve a secret value with caching and validation.
        
        Args:
            key: The secret key to retrieve
            default: Default value if secret is not found (only for optional secrets)
            
        Returns:
            The secret value or default
            
        Raises:
            RuntimeError: If a required secret is missing
        """
        # Check cache first
        if key in self._secrets_cache:
            return self._secrets_cache[key]
            
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
            
        # Cache the value
        if value:
            self._secrets_cache[key] = value
            
        return value
    
    def _get_from_secret_manager(self, key: str) -> Optional[str]:
        """
        Retrieve secret from external secret manager.
        Implement this based on your infrastructure (AWS Secrets Manager, HashiCorp Vault, etc.)
        """
        # Example implementation for AWS Secrets Manager (requires boto3)
        # try:
        #     import boto3
        #     client = boto3.client('secretsmanager')
        #     response = client.get_secret_value(SecretId=f'nextreel/{key.lower()}')
        #     return json.loads(response['SecretString']).get(key)
        # except Exception:
        #     pass
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
                    # Mask the secret in logs
                    masked = value[:4] + '*' * (len(value) - 8) + value[-4:] if len(value) > 8 else '***'
                    logger.info(f"Secret '{key}' validated: {masked}")
            except RuntimeError:
                missing_secrets.append(f"{key}: {description}")
        
        if missing_secrets:
            logger.error(f"Missing required secrets: {', '.join(missing_secrets)}")
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
        self.get_secret.cache_clear()
        logger.info("Secrets cache cleared")


# Singleton instance
secrets_manager = SecretsManager()