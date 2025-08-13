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


import asyncio
import time
import logging
from typing import Optional, Dict, Any, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from aiomysql import Pool, Connection, DictCursor


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
    
    # Pool settings
    min_size: int = 5
    max_size: int = 20
    
    # Connection lifecycle
    connect_timeout: int = 10
    pool_recycle: int = 3600  # Recycle connections after 1 hour
    echo: bool = False
    
    # Health checks
    pool_pre_ping: bool = True
    ping_interval: int = 30
    
    # Retry settings
    max_retries: int = 3
    retry_delay: float = 0.5
    retry_backoff: float = 2.0
    
    # Circuit breaker
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: int = 60
    
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
    """Create an SSL context if a valid certificate path is provided."""
    if ssl_cert_path and os.path.isfile(ssl_cert_path):
        context = ssl.create_default_context(cafile=ssl_cert_path)
        logger.info("SSL context created successfully.")
        return context
    elif ssl_cert_path:
        logger.error("SSL certificate file not found at %s", ssl_cert_path)
    return None  # Return None if no valid SSL context


class DatabaseConnectionPool:
    """Production-ready database connection pool with health checks and monitoring"""
    
    def __init__(self, db_config):
        # Convert old config format to new PoolConfig
        ssl_cert_path = Config.get_ssl_cert_path() if hasattr(Config, 'get_ssl_cert_path') else None
        use_ssl = Config.use_ssl() if hasattr(Config, 'use_ssl') else False
        
        # Create pool config from old db_config
        self.config = PoolConfig(
            host=db_config['host'],
            port=db_config.get('port', 3306),
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            min_size=int(os.getenv('POOL_MIN_SIZE', Config.POOL_MIN_SIZE)),
            max_size=int(os.getenv('POOL_MAX_SIZE', Config.POOL_MAX_SIZE)),
            connect_timeout=int(os.getenv('DB_CONNECT_TIMEOUT', 10)),
            pool_recycle=int(os.getenv('DB_POOL_RECYCLE', 3600)),
            ssl_cert_path=ssl_cert_path,
            use_ssl=use_ssl,
            pool_pre_ping=os.getenv('DB_POOL_PRE_PING', 'true').lower() == 'true',
            ping_interval=int(os.getenv('DB_PING_INTERVAL', 30)),
            max_retries=int(os.getenv('DB_MAX_RETRIES', 3)),
            circuit_breaker_threshold=int(os.getenv('DB_CIRCUIT_BREAKER_THRESHOLD', 5)),
            circuit_breaker_timeout=int(os.getenv('DB_CIRCUIT_BREAKER_TIMEOUT', 60))
        )
        
        self.pool: Optional[Pool] = None
        self.metrics = PoolMetrics()
        self.circuit_breaker = CircuitBreaker(
            self.config.circuit_breaker_threshold,
            self.config.circuit_breaker_timeout
        )
        self._connection_creation_times: Dict[int, datetime] = {}
        self._ssl_context = _create_ssl_context(self.config.ssl_cert_path) if self.config.use_ssl else None
        self._health_check_task: Optional[asyncio.Task] = None
        self._shutdown = False
    
    async def init_pool(self):
        """Initialize the connection pool"""
        if self.circuit_breaker.is_open():
            raise Exception("Database circuit breaker is open")
            
        try:
            start_time = time.time()
            logger.info(f"Initializing database pool: {self.config.host}:{self.config.port}/{self.config.database}")
            
            self.pool = await aiomysql.create_pool(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                db=self.config.database,
                minsize=self.config.min_size,
                maxsize=self.config.max_size,
                connect_timeout=self.config.connect_timeout,
                echo=self.config.echo,
                ssl=self._ssl_context,
                cursorclass=DictCursor,
                pool_recycle=self.config.pool_recycle,
                autocommit=False
            )
            
            # Test the pool
            await self._validate_pool()
            
            # Start health check task
            if self.config.pool_pre_ping:
                self._health_check_task = asyncio.create_task(self._health_check_loop())
            
            self.circuit_breaker.record_success()
            
            end_time = time.time()
            logger.info(f"Database pool initialized successfully in {end_time - start_time:.2f}s (min={self.config.min_size}, max={self.config.max_size})")
            
        except Exception as e:
            self.circuit_breaker.record_failure()
            logger.error(f"Failed to initialize database pool: {e}")
            raise
    
    async def _validate_pool(self):
        """Validate pool connectivity"""
        async with self.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                result = await cursor.fetchone()
                if result != {'1': 1}:
                    raise Exception("Pool validation failed")
    
    async def _health_check_loop(self):
        """Periodic health check for connections"""
        while not self._shutdown:
            try:
                await asyncio.sleep(self.config.ping_interval)
                await self._check_pool_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Health check failed: {e}")
    
    async def _check_pool_health(self):
        """Check health of all connections in pool"""
        if not self.pool:
            return
            
        try:
            # Get pool statistics
            self.metrics.idle_connections = self.pool.freesize
            self.metrics.active_connections = self.pool.size - self.pool.freesize
            
            # Ping a connection
            async with self.acquire() as conn:
                await conn.ping()
                self.metrics.health_checks_passed += 1
                
        except Exception as e:
            self.metrics.health_checks_failed += 1
            logger.warning(f"Pool health check failed: {e}")
    
    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Connection]:
        """Acquire a connection from the pool with retry logic"""
        if self.circuit_breaker.is_open():
            raise Exception("Database circuit breaker is open")
            
        if not self.pool:
            await self.init_pool()
            
        connection = None
        retry_count = 0
        last_error = None
        
        try:
            while retry_count < self.config.max_retries:
                try:
                    # Acquire connection
                    connection = await self.pool.acquire()
                    conn_id = id(connection)
                    
                    # Check if connection needs recycling
                    if conn_id in self._connection_creation_times:
                        age = (datetime.now() - self._connection_creation_times[conn_id]).seconds
                        if age > self.config.pool_recycle:
                            logger.debug(f"Recycling connection {conn_id} (age: {age}s)")
                            await connection.ensure_closed()
                            connection = await self.pool.acquire()
                            self.metrics.connections_recycled += 1
                            self._connection_creation_times[id(connection)] = datetime.now()
                    else:
                        self._connection_creation_times[conn_id] = datetime.now()
                        self.metrics.connections_created += 1
                    
                    # Ping connection if configured
                    if self.config.pool_pre_ping:
                        await connection.ping()
                    
                    self.circuit_breaker.record_success()
                    yield connection
                    break
                    
                except Exception as e:
                    last_error = e
                    retry_count += 1
                    
                    if connection:
                        # Return bad connection to pool
                        self.pool.release(connection)
                        connection = None
                    
                    if retry_count < self.config.max_retries:
                        delay = self.config.retry_delay * (self.config.retry_backoff ** retry_count)
                        logger.warning(f"Database connection failed, retrying in {delay}s: {e}")
                        await asyncio.sleep(delay)
                    else:
                        self.circuit_breaker.record_failure()
                        self.metrics.connections_failed += 1
                        self.metrics.last_error = str(e)
                        self.metrics.last_error_time = datetime.now()
                        raise last_error
                        
        finally:
            if connection:
                self.pool.release(connection)
    
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Connection]:
        """Execute operations in a transaction"""
        async with self.acquire() as connection:
            await connection.begin()
            try:
                yield connection
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
    
    async def execute(self, query: str, params: Optional[tuple] = None, fetch: str = 'one') -> Any:
        """Execute a query with automatic connection management"""
        start_time = time.time()
        
        try:
            async with self.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(query, params)
                    
                    if fetch == 'one':
                        result = await cursor.fetchone()
                    elif fetch == 'all':
                        result = await cursor.fetchall()
                    elif fetch == 'many':
                        result = await cursor.fetchmany()
                    else:  # fetch == 'none'
                        result = cursor.rowcount
                    
                    # Update metrics
                    query_time = time.time() - start_time
                    self.metrics.queries_executed += 1
                    self.metrics.avg_query_time = (
                        (self.metrics.avg_query_time * (self.metrics.queries_executed - 1) + query_time)
                        / self.metrics.queries_executed
                    )
                    self.metrics.max_query_time = max(self.metrics.max_query_time, query_time)
                    
                    return result
                    
        except Exception as e:
            self.metrics.queries_failed += 1
            logger.error(f"Query execution failed: {e}")
            raise
    
    async def execute_many(self, query: str, params_list: list) -> int:
        """Execute multiple queries efficiently"""
        rows_affected = 0
        
        async with self.transaction() as connection:
            async with connection.cursor() as cursor:
                for params in params_list:
                    await cursor.execute(query, params)
                    rows_affected += cursor.rowcount
                    
        return rows_affected
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get pool metrics"""
        return {
            'pool_size': self.pool.size if self.pool else 0,
            'free_connections': self.pool.freesize if self.pool else 0,
            'connections_created': self.metrics.connections_created,
            'connections_failed': self.metrics.connections_failed,
            'connections_recycled': self.metrics.connections_recycled,
            'queries_executed': self.metrics.queries_executed,
            'queries_failed': self.metrics.queries_failed,
            'avg_query_time_ms': self.metrics.avg_query_time * 1000,
            'max_query_time_ms': self.metrics.max_query_time * 1000,
            'health_checks_passed': self.metrics.health_checks_passed,
            'health_checks_failed': self.metrics.health_checks_failed,
            'circuit_breaker_state': self.circuit_breaker.state,
            'circuit_breaker_trips': self.metrics.circuit_breaker_trips,
            'last_error': self.metrics.last_error,
            'last_error_time': self.metrics.last_error_time.isoformat() if self.metrics.last_error_time else None
        }

    # Legacy methods for backward compatibility
    async def get_async_connection(self):
        """Legacy method - prefer using acquire() context manager"""
        if not self.pool:
            await self.init_pool()
        connection = await self.pool.acquire()
        return connection

    async def release_async_connection(self, conn):
        """Legacy method - prefer using acquire() context manager"""
        self.pool.release(conn)

    async def close_pool(self):
        """Close the pool and cleanup"""
        self._shutdown = True
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            logger.info("Database pool closed")

    def __repr__(self) -> str:
        return (
            f"<DatabaseConnectionPool "
            f"host={self.config.host} "
            f"database={self.config.database} "
            f"size={self.pool.size if self.pool else 0}/"
            f"{self.config.max_size}>"
        )


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
