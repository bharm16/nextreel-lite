"""Legacy query constants and pool helpers.

The query executor abstraction previously defined here was removed. Callers
should use ``DatabaseConnectionPool.execute(...)`` directly and catch
``database.errors.DatabaseError`` where fallback behavior is intentional.
"""

from database.errors import DatabaseError
from logging_config import get_logger

# Query to fetch IMDb details of a watched movie
GET_WATCHED_MOVIE_DETAILS = """
SELECT 
    `title.basics`.primaryTitle AS title,
    `title.basics`.genres,
    `title.crew`.directors,
    `title.crew`.writers,
    `title.basics`.runtimeMinutes AS runtimes,
    `title.ratings`.averageRating AS rating,
    `title.ratings`.numVotes AS votes,
    `title.basics`.slug AS slug  -- Added slug to the SELECT statement

FROM 
    `title.basics`
JOIN
    `title.ratings` ON `title.basics`.tconst = `title.ratings`.tconst
JOIN
    `title.crew` ON `title.basics`.tconst = `title.crew`.tconst
WHERE 
    `title.basics`.tconst = %s;
"""

# Queries for update_title_basics_if_empty function
CHECK_TITLE_BASICS = "SELECT plot, poster_url, language FROM `title.basics` WHERE tconst=%s;"
UPDATE_TITLE_BASICS = """
    UPDATE `title.basics`
    SET plot = %s, poster_url = %s, language = %s
    WHERE tconst = %s;
"""

# Query for update_missing_title_info function
SELECT_MISSING_TITLE_INFO = """
SELECT tconst
    FROM `title.basics`
    WHERE (plot IS NULL OR poster_url IS NULL OR language IS NULL)
    AND titleType = 'movie'
"""

# Query to get all movies by an actor
GET_ALL_MOVIES_BY_ACTOR_QUERY = """
    SELECT tb.*
    FROM `title.basics` tb
    JOIN `title.principals` tp ON tb.tconst = tp.tconst
    WHERE tp.nconst = %s 
    AND tb.titleType = 'movie'
    AND tp.category = 'actor'
"""

GET_NCONST_FROM_ACTOR_NAME_QUERY = """
SELECT nconst FROM `name.basics`
WHERE primaryName = %s
LIMIT 1
"""

# Assume necessary imports and SQL queries defined above remain unchanged

logger = get_logger(__name__)

__all__ = [
    "CHECK_TITLE_BASICS",
    "DatabaseError",
    "GET_ALL_MOVIES_BY_ACTOR_QUERY",
    "GET_NCONST_FROM_ACTOR_NAME_QUERY",
    "GET_WATCHED_MOVIE_DETAILS",
    "SELECT_MISSING_TITLE_INFO",
    "UPDATE_TITLE_BASICS",
    "init_pool",
]


async def init_pool():
    from database.pool import init_pool as _init_global_pool
    db_pool = await _init_global_pool()
    logger.info("Database connection pool initialized.")
    return db_pool
