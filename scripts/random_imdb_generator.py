import random

from config import create_connection


# Function to get a random row's value from a specific table and column
def get_random_row_value(table_name, column_name):
    # Establish a database connection
    connection = create_connection()

    try:
        with connection.cursor() as cursor:
            # Find the total number of rows in the table
            cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
            total_rows = cursor.fetchone()[0]

            # Select a random row number
            random_row_num = random.randint(1, total_rows)

            # Fetch the value from the random row
            cursor.execute(f"SELECT {column_name} FROM `{table_name}` LIMIT {random_row_num - 1}, 1")
            random_value = cursor.fetchone()[0]

            # Fetch the entire row based on the random value
            cursor.execute(f"SELECT * FROM `{table_name}` WHERE {column_name} = %s", (random_value,))
            random_row = cursor.fetchone()
            column_names = [desc[0] for desc in cursor.description]

        return dict(zip(column_names, random_row))
    finally:
        # Ensure the connection is closed
        connection.close()


# Function to get the rating by tconst
def get_rating_by_tconst(tconst):
    # Use the same connection function
    connection = create_connection()

    try:
        with connection.cursor() as cursor:
            # Fetch the rating information based on the tconst
            cursor.execute("SELECT * FROM `title.ratings` WHERE tconst = %s", (tconst,))
            rating_info = cursor.fetchone()

        return rating_info
    finally:
        # Ensure the connection is closed
        connection.close()


# Example usage
try:
    # Get a random row from the 'title.basics' table
    random_row = get_random_row_value('title.basics', 'tconst')
    print(random_row)

    if random_row:
        tconst = random_row['tconst']
        # Get the rating info for the tconst
        rating_info = get_rating_by_tconst(tconst)
        print(f"Rating information for tconst {tconst}:")
        print(rating_info)
    else:
        print("No random row was found.")
except Exception as e:
    print(f"An error occurred: {e}")
