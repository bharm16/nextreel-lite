# queries.py
import time

import pymysql
from urllib.parse import urlparse
import os
import mysql.connector
from flask.cli import load_dotenv

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


def execute_query(db_config, query, params=None, fetch='one'):
    start_time = time.time()  # Start the timer

    conn = pymysql.connect(**db_config)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

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

    cursor.close()
    conn.close()
    return result


def get_db_connection(db_config):
    """Establish a connection to the database."""
    return pymysql.connect(
        host=db_config['host'],
        user=db_config['user'],
        password=db_config['caching_sa_password'],
        database=db_config['imdb']
    )


# First, ensure you have the mysql-connector-python package installed.
# You can install it via pip:
# pip install mysql-connector-python


# Load environment variables from a .env file if present
load_dotenv()


def create_db_connection():
    """Create a connection to the JawsDB database."""
    jawsdb_url = os.getenv('JAWSDB_URL')
    if not jawsdb_url:
        raise ValueError("JAWSDB_URL is not set in environment variables.")

    parsed_url = urlparse(jawsdb_url)
    username = parsed_url.username
    password = parsed_url.password
    host = parsed_url.hostname
    database = parsed_url.path[1:]  # Remove the leading '/' from the path
    port = parsed_url.port or 3306  # Default to port 3306 if not specified

    connection = mysql.connector.connect(
        user=username,
        password=password,
        host=host,
        database=database,
        port=port
    )
    return connection


def perform_query(connection, query):
    """Perform a database query and return the results."""
    cursor = connection.cursor()
    cursor.execute(query)
    results = cursor.fetchall()
    cursor.close()
    return results


# Replace 'your_table_name' with the actual table name you want to query
# sample_query = "SELECT * FROM your_table_name"

# Main execution
if __name__ == "__main__":
    try:
        # Create a database connection
        db_connection = create_db_connection()

        # Perform a sample query and print results
        query_results = execute_query(db_connection, sample_query)
        for row in query_results:
            print(row)

        # Close the connection
        db_connection.close()
    except mysql.connector.Error as err:
        print(f"Error: {err}")
    except Exception as e:
        print(f"An error occurred: {e}")
