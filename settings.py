"""Application settings — unified Config class composed from domain configs.

Modules should import ``Config`` from here for backward compatibility.
For focused access, import directly from ``config.database``,
``config.session``, or ``config.api``.
"""

import os
from dotenv import load_dotenv
from logging_config import get_logger

logger = get_logger(__name__)

flask_env = os.getenv("FLASK_ENV", "development")
logger.debug("FLASK_ENV is set to: %s", flask_env)

env_file = ".env.development" if flask_env == "development" else ".env"
load_dotenv(dotenv_path=env_file)
logger.debug("Loaded .env file: %s", env_file)
logger.debug("Database Host from environment: %s", os.getenv("DB_HOST"))

# Import domain-specific configs
from config.database import DatabaseConfig
from config.session import SessionConfig
from config.api import ApiConfig


class Config(DatabaseConfig, SessionConfig, ApiConfig):
    """Unified configuration class.

    Inherits from the three domain configs so that all existing attribute
    lookups (``Config.get_db_config()``, ``Config.SECRET_KEY``, etc.)
    continue to work unchanged.
    """

    # Dynamic properties for backward compatibility with Quart's from_object()
    @property
    def TMDB_API_KEY(self):
        return self.get_tmdb_api_key()


# Re-export DatabaseConnectionPool and pool helpers for backward compatibility.
# The canonical implementation now lives in database/pool.py.
from database.pool import DatabaseConnectionPool, init_pool, get_pool, close_pool
