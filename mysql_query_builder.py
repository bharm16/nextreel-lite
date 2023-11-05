# queries.py
import os
import time

from config import create_connection

os.chdir(os.path.dirname(os.path.abspath(__file__)))


print(f"Current working directory: {os.getcwd()}")
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
    `title.ratings`.numVotes AS votes
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




def execute_query(query, params=None, fetch='one'):
    # Start the timer to measure the execution time of the query
    start_time = time.time()

    # Try to establish a database connection
    conn = create_connection()
    if not conn:
        # If connection is not established, return None or raise an error
        print("Failed to establish database connection.")
        return None

    try:
        # Create a cursor and execute the query
        with conn.cursor() as cursor:
            cursor.execute(query, params)

            # Fetch the result based on the fetch type
            if fetch == 'one':
                result = cursor.fetchone()
            elif fetch == 'all':
                result = cursor.fetchall()
            elif fetch == 'none':
                # For queries that do not require data fetching
                conn.commit()
                result = None
            else:
                raise ValueError(f"Invalid fetch parameter: {fetch}")

        # Stop the timer and calculate the elapsed time
        end_time = time.time()
        elapsed_time = end_time - start_time
        # Optionally, you can print or log the execution time
        # print(f"Execution time for query: {elapsed_time:.5f} seconds")

        return result

    except Exception as e:
        # If an error occurs during the query execution, print or log the error
        print(f"An error occurred while executing the query: {e}")
        return None

    finally:
        # Ensure that the connection is closed even if an error occurs
        if conn:
            conn.close()


# Code to test the execute_query function

# A test parameter for the query
test_tconst = 'tt0111161'  # Replace with a valid tconst value from your database

# Call the function with the GET_WATCHED_MOVIE_DETAILS query
result = execute_query(GET_WATCHED_MOVIE_DETAILS, params=(test_tconst,), fetch='one')

# Check if the result is not None
if result:
    print("Query executed successfully.")
    print(result)
else:
    print("Query execution failed or returned no results.")