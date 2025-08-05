import os
import ssl
import aiomysql
from dotenv import load_dotenv
import time
import logging
from logging_config import get_logger

logger = get_logger(__name__)

flask_env = os.getenv('FLASK_ENV', 'development')
# Determine which .env file to load based on FLASK_ENV
# flask_env = os.getenv('FLASK_ENV', 'production')
logger.debug("FLASK_ENV is set to: %s", flask_env)

env_file = '.env.development' if flask_env == 'development' else '.env'
load_dotenv(dotenv_path=env_file)
logger.debug("Loaded .env file: %s", env_file)
logger.debug("Database Host from environment: %s", os.getenv('DB_HOST'))





class Config:
    # Common configurations
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY')
    TMDB_API_KEY = os.getenv('TMDB_API_KEY')

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = flask_env != 'development'

    # Expose production database configuration for scripts that need it
    STACKHERO_DB_CONFIG = {
        'host': os.getenv('STACKHERO_DB_HOST'),
        'user': os.getenv('STACKHERO_DB_USER'),
        'password': os.getenv('STACKHERO_DB_PASSWORD'),
        'database': os.getenv('STACKHERO_DB_NAME'),
        'port': int(os.getenv('STACKHERO_DB_PORT', 3306)),
    }

    # Dynamically switch database configurations based on FLASK_ENV
    @staticmethod
    def get_db_config():
        if flask_env == 'development':
            return {
                'host': os.getenv('DB_HOST', '127.0.0.1'),
                'user': os.getenv('DB_USER', 'root'),
                'password': os.getenv('DB_PASSWORD', ''),
                'database': os.getenv('DB_NAME', 'imdb'),
                'port': int(os.getenv('DB_PORT', 3306)),
            }
        else:  # Production configuration
            return {
                'host': os.getenv('STACKHERO_DB_HOST'),
                'user': os.getenv('STACKHERO_DB_USER'),
                'password': os.getenv('STACKHERO_DB_PASSWORD'),
                'database': os.getenv('STACKHERO_DB_NAME'),
                'port': int(os.getenv('STACKHERO_DB_PORT', 3306)),
            }

    # SSL Certificate Path
    @staticmethod
    def get_ssl_cert_path():
        return os.getenv('SSL_CERT_PATH') or os.path.join(os.path.dirname(__file__), 'isrgroot.pem')

    # Pool configurations for aiomysql
    POOL_MIN_SIZE = 5  # Minimum number of connections in the pool
    POOL_MAX_SIZE = 50  # Maximum number of connections in the pool

    # SSL usage based on environment
    @staticmethod
    def use_ssl():
        return flask_env != 'development'


def _create_ssl_context(ssl_cert_path):
    """Create an SSL context if a valid certificate path is provided."""
    if ssl_cert_path and os.path.isfile(ssl_cert_path):
        context = ssl.create_default_context(cafile=ssl_cert_path)
        logger.info("SSL context created successfully.")
        return context
    elif ssl_cert_path:
        logger.error("SSL certificate file not found at %s", ssl_cert_path)
    return None  # Return None if no valid SSL context


class DatabaseConnectionPool:
    def __init__(self, db_config):
        self.db_config = db_config
        self.ssl_ctx = _create_ssl_context(Config.get_ssl_cert_path()) if Config.use_ssl() else None
        self.pool = None

    async def init_pool(self):
        start_time = time.time()
        self.pool = await aiomysql.create_pool(
            minsize=Config.POOL_MIN_SIZE,
            maxsize=Config.POOL_MAX_SIZE,
            host=self.db_config['host'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            db=self.db_config['database'],
            port=self.db_config['port'],
            ssl=self.ssl_ctx,
            cursorclass=aiomysql.DictCursor
        )
        end_time = time.time()
        logger.debug(
            "Connection pool initialized in %.2f seconds", end_time - start_time
        )

    async def get_async_connection(self):
        if not self.pool:
            await self.init_pool()
        connection = await self.pool.acquire()
        return connection

    async def release_async_connection(self, conn):
        self.pool.release(conn)

    async def close_pool(self):
        self.pool.close()
        await self.pool.wait_closed()


# Asynchronous usage example
async def main():
    db_config = Config.get_db_config()
    db_pool = DatabaseConnectionPool(db_config)
    conn = await db_pool.get_async_connection()
    # Perform database operations...
    await db_pool.release_async_connection(conn)
    await db_pool.close_pool()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
