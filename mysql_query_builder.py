# queries.py
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# print(f"Current working directory: {os.getcwd()}")
# print(f"Resolved SSL certificate path: {Config.SSL_CERT_PATH}")

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

from config import Config, DatabaseConnectionPool
import asyncio
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Assume SQL query definitions remain the same

class DatabaseQueryExecutor:
    def __init__(self, db_pool):
        if not isinstance(db_pool, DatabaseConnectionPool):
            raise ValueError("db_pool must be an instance of DatabaseConnectionPool")
        self.db_pool = db_pool

    async def execute_async_query(self, query, params=None, fetch='one'):
        start_time = time.time()  # Start timing before acquiring connection
        conn = await self.db_pool.get_async_connection()
        if not conn:
            logging.error("Failed to acquire a database connection.")
            return None

        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params)
                if fetch == 'one':
                    result = await cursor.fetchone()
                elif fetch == 'all':
                    result = await cursor.fetchall()
                elif fetch == 'none':
                    await conn.commit()
                    result = None
                else:
                    raise ValueError(f"Invalid fetch parameter: {fetch}")
                return result
        except Exception as e:
            logging.error(f"An error occurred while executing the query: {e}")
            return None
        finally:
            await self.db_pool.release_async_connection(conn)
            end_time = time.time()  # End timing after releasing connection
            logging.info(f"Query and connection handling executed in {end_time - start_time:.2f} seconds.")


async def init_pool():
    db_pool = DatabaseConnectionPool(Config.get_db_config())
    await db_pool.init_pool()
    logging.info("Database connection pool initialized.")
    return db_pool


async def main():
    # Initialize the connection pool
    db_pool = await init_pool()

    # Pass the initialized pool to DatabaseQueryExecutor
    query_executor = DatabaseQueryExecutor(db_pool)

    # Your query execution logic remains the same
    # Ensure to close the pool at the end of your program
    await db_pool.close_pool()

if __name__ == "__main__":
    asyncio.run(main())