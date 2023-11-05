# queries.py
import time

import pymysql
from urllib.parse import urlparse
import os
import mysql.connector
from flask.cli import load_dotenv

from config import create_connection

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
    start_time = time.time()  # Start the timer

    # Establish a database connection using the create_connection function from your config
    conn = create_connection()

    with conn.cursor() as cursor:
        cursor.execute(query, params)

        if fetch == 'one':
            result = cursor.fetchone()
        elif fetch == 'all':
            result = cursor.fetchall()
        elif fetch == 'none':  # For queries like INSERT, UPDATE, DELETE
            conn.commit()
            result = None

    end_time = time.time()  # Stop the timer
    elapsed_time = end_time - start_time  # Calculate elapsed time
    # print(f"Execution time for query: {elapsed_time:.5f} seconds")

    conn.close()
    return result
