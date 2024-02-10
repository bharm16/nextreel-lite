import pymysql
import csv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Database connection configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'caching_sha2_password',
    'database': 'imdb'
}


def read_tsv_and_update_database(tsv_file_path, db_config):
    """
    Reads a TSV file and updates the averageRating and numVotes in the database.
    Logs each update operation.

    :param tsv_file_path: Path to the TSV file containing the updates.
    :param db_config: Dictionary with the database connection configuration.
    """
    try:
        # Establish a database connection
        connection = pymysql.connect(
            host=db_config['host'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            cursorclass=pymysql.cursors.DictCursor  # Use DictCursor to work with column names
        )
        logging.info("Successfully connected to database.")

        with connection.cursor() as cursor:
            # Open the TSV file for reading
            with open(tsv_file_path, 'r', encoding='utf-8') as tsvfile:
                reader = csv.DictReader(tsvfile, delimiter='\t')
                for row in reader:
                    # Extract data from the current row
                    tconst = row['tconst']
                    averageRating = row['averageRating']
                    numVotes = row['numVotes']

                    # Prepare the UPDATE statement
                    update_sql = """
                    UPDATE `title.ratings`
                    SET `averageRating` = %s, `numVotes` = %s
                    WHERE `tconst` = %s
                    """

                    # Log the update operation
                    logging.info(f"Updating {tconst} with averageRating {averageRating} and numVotes {numVotes}.")

                    # Execute the update query
                    cursor.execute(update_sql, (averageRating, numVotes, tconst))

                # Commit the changes to the database
                connection.commit()
                logging.info("Database updated successfully.")

    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        if connection:
            # Make sure to close the database connection
            connection.close()
            logging.info("Database connection closed.")


# Path to your TSV file - ensure this path is correct
tsv_file_path = 'title.ratings.tsv'  # Update this path as necessary

# Call the function with the path to your TSV file and database configuration
read_tsv_and_update_database(tsv_file_path, db_config)
