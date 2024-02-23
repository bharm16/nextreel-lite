import os
import ssl
import aiomysql
from flask.cli import load_dotenv
import time
import logging

# Load environment variables from .env file
load_dotenv()

# Set up basic configuration for logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

class Config:
    UPSTASH_REDIS_URL = os.getenv('UPSTASH_REDIS_URL')

    SECRET_KEY = os.getenv('FLASK_SECRET_KEY')
    TMDB_API_KEY = os.getenv('TMDB_API_KEY')

    @staticmethod
    def get_ssl_cert_path():
        return os.getenv('SSL_CERT_PATH') or os.path.join(os.path.dirname(__file__), 'isrgroot.pem')

    # Database configurations
    STACKHERO_DB_CONFIG = {
        'host': os.getenv('STACKHERO_DB_HOST'),
        'user': os.getenv('STACKHERO_DB_USER'),
        'password': os.getenv('STACKHERO_DB_PASSWORD'),
        'database': os.getenv('STACKHERO_DB_NAME'),
        'port': int(os.getenv('STACKHERO_DB_PORT', 3306))
    }

    # Pool configurations for aiomysql
    POOL_MIN_SIZE = 5  # Minimum number of connections in the pool
    POOL_MAX_SIZE = 50  # Maximum number of connections in the pool

def _create_ssl_context(ssl_cert_path):
    if os.path.isfile(ssl_cert_path):
        context = ssl.create_default_context(cafile=ssl_cert_path)
        logging.info("SSL context created successfully.")
        return context
    logging.error(f"SSL certificate file not found at {ssl_cert_path}")
    return None

class DatabaseConnectionPool:
    def __init__(self, db_config):
        self.db_config = db_config
        self.ssl_ctx = _create_ssl_context(Config.get_ssl_cert_path())
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
        logging.info(f"Connection pool initialized in {end_time - start_time:.2f} seconds.")

    async def get_async_connection(self):
        if not self.pool:
            await self.init_pool()
        start_time = time.time()
        connection = await self.pool.acquire()
        end_time = time.time()
        # logging.info(f"Acquired connection from pool in {end_time - start_time:.2f} seconds.")
        return connection

    async def release_async_connection(self, conn):
        start_time = time.time()
        self.pool.release(conn)
        end_time = time.time()
        # logging.info(f"Released connection back to pool in {end_time - start_time:.2f} seconds.")

    async def close_pool(self):
        start_time = time.time()
        self.pool.close()
        await self.pool.wait_closed()
        end_time = time.time()
        # logging.info(f"Connection pool closed in {end_time - start_time:.2f} seconds.")

# Asynchronous usage example
async def main():
    db_pool = DatabaseConnectionPool(Config.STACKHERO_DB_CONFIG)
    conn = await db_pool.get_async_connection()
    # Perform database operations...
    await db_pool.release_async_connection(conn)
    await db_pool.close_pool()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
