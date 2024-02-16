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

from config import Config, DatabaseConnection


class DatabaseQueryExecutor:
    def __init__(self, db_config):
        self.db_connection = DatabaseConnection(db_config)

    async def execute_async_query(self, query, params=None, fetch='one'):
        conn = await self.db_connection.create_async_connection()
        if not conn:
            return None

        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params)
                if fetch == 'one':
                    return await cursor.fetchone()
                elif fetch == 'all':
                    return await cursor.fetchall()
                elif fetch == 'none':
                    await conn.commit()
                    return None
                else:
                    raise ValueError(f"Invalid fetch parameter: {fetch}")
        except Exception as e:
            print(f"An error occurred: {e}")
            return None
        finally:
            conn.close()

    def execute_sync_query(self, query, params=None, fetch='one'):
        conn = self.db_connection.create_sync_connection()
        if not conn:
            return None

        try:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                if fetch == 'one':
                    return cursor.fetchone()
                elif fetch == 'all':
                    return cursor.fetchall()
                elif fetch == 'none':
                    conn.commit()
                    return None
                else:
                    raise ValueError(f"Invalid fetch parameter: {fetch}")
        except Exception as e:
            print(f"An error occurred: {e}")
            return None
        finally:
            if conn:
                conn.close()

# Test the DatabaseQueryExecutor class
if __name__ == "__main__":
    db_config = Config.STACKHERO_DB_CONFIG
    query_executor = DatabaseQueryExecutor(db_config)

    test_tconst = 'tt0111161'  # Replace with a valid tconst value from your database
    # Test the synchronous query execution
    sync_result = query_executor.execute_sync_query(GET_WATCHED_MOVIE_DETAILS, params=(test_tconst,), fetch='one')
    if sync_result:
        print("Sync Query executed successfully.")
        print(sync_result)
    else:
        print("Sync Query execution failed or returned no results.")

    # Test the asynchronous query execution
    async def async_query():
        async_result = await query_executor.execute_async_query(GET_WATCHED_MOVIE_DETAILS, params=(test_tconst,), fetch='one')
        if async_result:
            print("Async Query executed successfully.")
            print(async_result)
        else:
            print("Async Query execution failed or returned no results.")

    import asyncio
    asyncio.run(async_query())