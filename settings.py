import os
import ssl
import aiomysql
import secrets
from dotenv import load_dotenv
import time
import logging
from logging_config import get_logger
from secrets_manager import secrets_manager

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
    @staticmethod
    def get_flask_secret_key():
        """Get Flask secret key from secure source."""
        return secrets_manager.get_secret('FLASK_SECRET_KEY')
    
    @staticmethod
    def get_tmdb_api_key():
        """Get TMDB API key from secure source."""
        return secrets_manager.get_secret('TMDB_API_KEY')
    
    # Dynamic properties for backward compatibility
    @property
    def SECRET_KEY(self):
        return self.get_flask_secret_key()
    
    @property
    def TMDB_API_KEY(self):
        return self.get_tmdb_api_key()

    # Session Security Configuration
    SECRET_KEY = secrets_manager.get_secret('FLASK_SECRET_KEY')
    
    # Session Cookie Security  
    SESSION_COOKIE_NAME = 'session'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'  # or 'Strict' for higher security
    
    # Force HTTPS in production
    @property
    def SESSION_COOKIE_SECURE(self):
        """Enable secure cookies in production."""
        env = os.getenv('FLASK_ENV', 'development')
        secure = env != 'development'
        if env == 'production' and not secure:
            logger.error("WARNING: Secure cookies disabled in production!")
        return secure
    
    # Additional security headers
    SESSION_COOKIE_DOMAIN = None if os.getenv('FLASK_ENV') != 'production' else os.getenv('COOKIE_DOMAIN', None)
    
    # Session timeouts
    SESSION_TIMEOUT_MINUTES = int(os.getenv('SESSION_TIMEOUT_MINUTES', 30))
    SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv('SESSION_IDLE_TIMEOUT_MINUTES', 15))
    SESSION_ROTATION_INTERVAL = int(os.getenv('SESSION_ROTATION_INTERVAL', 10))
    MAX_SESSION_DURATION_HOURS = int(os.getenv('MAX_SESSION_DURATION_HOURS', 24))
    
    # Redis session configuration
    SESSION_TYPE = 'redis'
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = False
    SESSION_KEY_PREFIX = 'session:'
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours in seconds

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
        """Get database configuration based on environment"""
        if flask_env == 'development':
            # Development configuration - local MySQL
            return {
                'host': os.getenv('DB_HOST', '127.0.0.1'),
                'user': os.getenv('DB_USER', 'root'),
                'password': os.getenv('DB_PASSWORD', ''),
                'database': os.getenv('DB_NAME', 'imdb'),
                'port': int(os.getenv('DB_PORT', 3306)),
            }
        else:
            # Production configuration - use production database variables
            # These can be your cloud provider's database or any production MySQL
            return {
                'host': os.getenv('PROD_DB_HOST', os.getenv('STACKHERO_DB_HOST', os.getenv('DB_HOST'))),
                'user': os.getenv('PROD_DB_USER', os.getenv('STACKHERO_DB_USER', os.getenv('DB_USER'))),
                'password': os.getenv('PROD_DB_PASSWORD', os.getenv('STACKHERO_DB_PASSWORD', os.getenv('DB_PASSWORD'))),
                'database': os.getenv('PROD_DB_NAME', os.getenv('STACKHERO_DB_NAME', os.getenv('DB_NAME'))),
                'port': int(os.getenv('PROD_DB_PORT', os.getenv('STACKHERO_DB_PORT', os.getenv('DB_PORT', 3306)))),
            }

    # SSL Certificate Path
    @staticmethod
    def get_ssl_cert_path():
        return os.getenv('SSL_CERT_PATH') or os.path.join(os.path.dirname(__file__), 'isrgroot.pem')

    # Pool configurations for aiomysql - Optimized for performance
    POOL_MIN_SIZE = 10  # Increased from 5 for better responsiveness
    POOL_MAX_SIZE = 30  # Reduced from 50 for better resource management

    # SSL usage based on environment
    @staticmethod
    def use_ssl():
        return flask_env != 'development'


import asyncio
import time
import logging
from typing import Optional, Dict, Any, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from aiomysql import Pool, Connection, DictCursor
import weakref
import atexit


@dataclass
class PoolConfig:
    """Database pool configuration"""
    # Connection settings (required fields first)
    host: str
    user: str
    password: str
    database: str
    # Optional connection settings
    port: int = 3306
    
    # Pool settings - Optimized for performance
    min_size: int = 10
    max_size: int = 30
    
    # Connection lifecycle - Optimized timeouts
    connect_timeout: int = 5  # Reduced from 10 for faster failure
    pool_recycle: int = 1800  # Reduced from 3600 (30 min instead of 1 hour)
    echo: bool = False
    
    # Health checks - More frequent for better reliability
    pool_pre_ping: bool = True
    ping_interval: int = 15  # Reduced from 30 for better detection
    
    # Retry settings - Faster failure recovery
    max_retries: int = 2  # Reduced from 3 for faster failure
    retry_delay: float = 0.5
    retry_backoff: float = 2.0
    
    # Circuit breaker - More responsive
    circuit_breaker_threshold: int = 3  # Reduced from 5 for faster protection
    circuit_breaker_timeout: int = 30   # Reduced from 60 for faster recovery
    
    # SSL settings
    ssl_cert_path: Optional[str] = None
    use_ssl: bool = False


@dataclass
class PoolMetrics:
    """Connection pool metrics"""
    connections_created: int = 0
    connections_closed: int = 0
    connections_recycled: int = 0
    connections_failed: int = 0
    
    queries_executed: int = 0
    queries_failed: int = 0
    
    active_connections: int = 0
    idle_connections: int = 0
    
    health_checks_passed: int = 0
    health_checks_failed: int = 0
    
    circuit_breaker_trips: int = 0
    
    avg_query_time: float = 0.0
    max_query_time: float = 0.0
    
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None


class CircuitBreaker:
    """Circuit breaker for database failures"""
    
    def __init__(self, threshold: int = 5, timeout: int = 60):
        self.threshold = threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open
        
    def record_success(self):
        """Record a successful operation"""
        self.failure_count = 0
        self.state = "closed"
        
    def record_failure(self):
        """Record a failed operation"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.failure_count >= self.threshold:
            self.state = "open"
            logger.error(f"Circuit breaker opened after {self.failure_count} failures")
            
    def is_open(self) -> bool:
        """Check if circuit is open"""
        if self.state == "closed":
            return False
            
        if self.state == "open":
            # Check if timeout has passed
            if self.last_failure_time:
                elapsed = (datetime.now() - self.last_failure_time).seconds
                if elapsed > self.timeout:
                    self.state = "half-open"
                    logger.info("Circuit breaker entering half-open state")
                    return False
            return True
            
        return False


def _create_ssl_context(ssl_cert_path):
    """Create an SSL context with enhanced security settings."""
    try:
        # If no certificate path, use system defaults
        if not ssl_cert_path or not os.path.isfile(ssl_cert_path):
            if flask_env == 'production':
                # In production, try system certificates
                context = ssl.create_default_context()
                logger.info("Using system default SSL certificates")
            else:
                # In development, SSL is optional
                logger.info("SSL certificate not found, SSL optional in development")
                return None
        else:
            # Use provided certificate
            context = ssl.create_default_context(cafile=ssl_cert_path)
            logger.info(f"Using SSL certificate: {ssl_cert_path}")
        
        # Configure context for MySQL
        context.check_hostname = False  # MySQL doesn't use hostname verification
        
        # Set verification mode based on environment
        if flask_env == 'production':
            context.verify_mode = ssl.CERT_REQUIRED
        else:
            # In development, make SSL optional
            context.verify_mode = ssl.CERT_NONE
            
        # Set minimum TLS version to 1.2
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        
        # Use strong ciphers only
        context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')
        
        logger.info("SSL context created with enhanced security settings")
        return context
        
    except Exception as e:
        logger.error("Failed to create SSL context: %s", e)
        if flask_env == 'production':
            raise  # Re-raise in production
        return None  # Continue without SSL in development


from secure_pool import SecureConnectionPool, SecurePoolConfig

class DatabaseConnectionPool:
    """Wrapper for backward compatibility with secure pool"""
    
    def __init__(self, db_config):
        # Convert to secure pool config
        self.secure_config = SecurePoolConfig(
            host=db_config['host'],
            port=db_config.get('port', 3306),
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            min_size=int(os.getenv('POOL_MIN_SIZE', 5)),
            max_size=int(os.getenv('POOL_MAX_SIZE', 20)),
            max_connections_per_user=int(os.getenv('MAX_CONN_PER_USER', 10)),
            max_connections_per_ip=int(os.getenv('MAX_CONN_PER_IP', 20)),
            connect_timeout=int(os.getenv('DB_CONNECT_TIMEOUT', 5)),
            query_timeout=int(os.getenv('DB_QUERY_TIMEOUT', 30)),
            pool_recycle=int(os.getenv('DB_POOL_RECYCLE', 900)),
            idle_timeout=int(os.getenv('DB_IDLE_TIMEOUT', 300)),
            max_queries_per_minute=int(os.getenv('MAX_QUERIES_PER_MIN', 5000)),
            max_queries_per_user_minute=int(os.getenv('MAX_USER_QUERIES_PER_MIN', 500)),
            ssl_cert_path=Config.get_ssl_cert_path() if hasattr(Config, 'get_ssl_cert_path') else None,
            use_ssl=Config.use_ssl() if hasattr(Config, 'use_ssl') else (flask_env == 'production'),
            slow_query_threshold=float(os.getenv('SLOW_QUERY_THRESHOLD', 1.0))
        )
        
        self.pool = SecureConnectionPool(self.secure_config)
    
    async def init_pool(self):
        """Initialize the pool"""
        await self.pool.init_pool()
    
    async def acquire(self, user_id=None, ip_address=None):
        """Acquire a connection"""
        return self.pool.acquire(user_id=user_id, ip_address=ip_address)
    
    async def execute(self, query, params=None, fetch='one', user_id=None):
        """Execute a query"""
        return await self.pool.execute_secure(
            query, params, user_id=user_id, fetch=fetch
        )
    
    async def close_pool(self):
        """Close the pool"""
        await self.pool.close_pool()
    
    async def get_metrics(self):
        """Get pool metrics"""
        return await self.pool.get_pool_status()

    # Legacy methods for backward compatibility
    async def get_async_connection(self):
        """Legacy method - prefer using acquire() context manager"""
        async with self.pool.acquire() as conn:
            return conn

    async def release_async_connection(self, conn):
        """Legacy method - connection is auto-released with context manager"""
        pass

    def __repr__(self) -> str:
        return (
            f"<SecureConnectionPool "
            f"host={self.secure_config.host} "
            f"database={self.secure_config.database}>"
        )


# Global pool instance management
_pool = None

async def init_pool():
    """Initialize the global database connection pool."""
    global _pool
    if _pool is None:
        db_config = Config.get_db_config()
        _pool = DatabaseConnectionPool(db_config)
        await _pool.init_pool()
        logger.info("Global database pool initialized")
    return _pool

async def get_pool():
    """Get the global database connection pool, initializing if needed."""
    global _pool
    if _pool is None:
        await init_pool()
    return _pool

async def close_pool():
    """Close the global database connection pool gracefully."""
    global _pool
    if _pool:
        try:
            await _pool.close_pool()
            _pool = None
            logger.info("Global database pool closed successfully")
        except Exception as e:
            logger.error(f"Error closing global database pool: {e}")
            _pool = None

# Register cleanup at exit
def _cleanup_pool_sync():
    """Synchronous cleanup for atexit."""
    global _pool
    if _pool:
        try:
            # Try to get the event loop
            loop = asyncio.get_event_loop()
            if loop and not loop.is_closed():
                loop.run_until_complete(close_pool())
        except Exception as e:
            logger.warning(f"Could not cleanly close pool at exit: {e}")

atexit.register(_cleanup_pool_sync)

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
