import csv
import logging
import os

from config import Config, DatabaseConnection

dbconfig = Config.STACKHERO_DB_CONFIG

# Use os.path.dirname to go up one level from the current script's directory
# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def read_tsv_and_update_database(tsv_file_path, db_connection):
    """
    Reads a TSV file and updates the averageRating and numVotes in the database.
    Logs each update operation.

    :param tsv_file_path: Path to the TSV file containing the updates.
    :param db_connection: DatabaseConnection instance for DB operations.
    """
    try:
        # Establish a database connection
        connection = db_connection.create_sync_connection()
        if not connection:
            logging.error("Failed to establish database connection.")
            return

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
tsv_file_path = 'scripts/title.ratings.tsv'  # Update this path as necessary

# Initialize the DatabaseConnection with the STACKHERO_DB_CONFIG
db_connection = DatabaseConnection(Config.STACKHERO_DB_CONFIG)

# Call the function with the path to your TSV file and the database connection instance
read_tsv_and_update_database(tsv_file_path, db_connection)
