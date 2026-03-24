"""Pool helpers for database initialization.

The query executor abstraction and legacy query constants previously defined
here were removed. Callers should use ``DatabaseConnectionPool.execute(...)``
directly and catch ``database.errors.DatabaseError`` where fallback behavior
is intentional.
"""

from database.errors import DatabaseError
from logging_config import get_logger

logger = get_logger(__name__)

__all__ = [
    "DatabaseError",
    "init_pool",
]


async def init_pool():
    from database.pool import init_pool as _init_global_pool
    db_pool = await _init_global_pool()
    logger.info("Database connection pool initialized.")
    return db_pool
