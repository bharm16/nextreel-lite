import os
import ssl
import aiomysql
import pymysql
import redis
from pymysql.cursors import DictCursor
from flask.cli import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    @staticmethod
    def get_ssl_cert_path():
        # Determine SSL certificate path
        return os.getenv('SSL_CERT_PATH') or os.path.join(os.path.dirname(__file__), 'isrgroot.pem')

    # Database configurations
    STACKHERO_DB_CONFIG = {
        'host': os.getenv('STACKHERO_DB_HOST'),
        'user': os.getenv('STACKHERO_DB_USER'),
        'password': os.getenv('STACKHERO_DB_PASSWORD'),
        'database': os.getenv('STACKHERO_DB_NAME'),
        'port': int(os.getenv('STACKHERO_DB_PORT', 3306))
    }

    # Redis configuration using UPSTASH_REDIS_URL
    UPSTASH_REDIS_URL = os.getenv('UPSTASH_REDIS_URL')

    SECRET_KEY = os.getenv('FLASK_SECRET_KEY')
    TMDB_API_KEY = os.getenv('TMDB_API_KEY')


def _create_ssl_context(ssl_cert_path):
    if os.path.isfile(ssl_cert_path):
        return ssl.create_default_context(cafile=ssl_cert_path)
    print(f"SSL certificate file not found at {ssl_cert_path}")
    return None


class DatabaseConnection:
    def __init__(self, db_config):
        self.db_config = db_config
        self.ssl_ctx = _create_ssl_context(Config.get_ssl_cert_path())

    async def create_async_connection(self):
        try:
            # print("Establishing asynchronous database connection...")
            connection = await aiomysql.connect(
                host=self.db_config['host'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                db=self.db_config['database'],
                port=self.db_config['port'],
                ssl=self.ssl_ctx,
                cursorclass=aiomysql.DictCursor
            )
            # print("Asynchronous database connection established successfully.")
            return connection
        except Exception as e:
            print(f"Asynchronous connection error: '{e}'")
            return None

    def create_sync_connection(self):
        try:
            print("Establishing synchronous database connection...")
            connection = pymysql.connect(
                host=self.db_config['host'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                port=self.db_config['port'],
                cursorclass=DictCursor,
                ssl=self.ssl_ctx
            )
            print("Synchronous database connection established successfully.")
            return connection
        except pymysql.MySQLError as err:
            print(f"Synchronous connection error: '{err}'")
            return None


# Synchronous usage
db_connection = DatabaseConnection(Config.STACKHERO_DB_CONFIG)
sync_conn = db_connection.create_sync_connection()
if sync_conn:
    print("Synchronous database connection established.")
    sync_conn.close()


# Asynchronous usage
async def main():
    async_conn = await db_connection.create_async_connection()
    if async_conn:
        # print("Asynchronous database connection established.")
        async_conn.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
