import csv
import logging
import os
import asyncio

from settings import Config, DatabaseConnectionPool

dbconfig = Config.STACKHERO_DB_CONFIG
database_pool = DatabaseConnectionPool(dbconfig)

# Use os.path.dirname to go up one level from the current script's directory
# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def read_tsv_and_update_database(tsv_file_path, database_pool):
    """
    Reads a TSV file and updates the averageRating and numVotes in the database.
    Logs each update operation.

    :param tsv_file_path: Path to the TSV file containing the updates.
    :param database_pool: DatabaseConnectionPool instance for DB operations.
    """
    connection = None
    try:
        # Establish a database connection from the pool
        connection = await database_pool.get_async_connection()
        logging.info("Successfully connected to database.")

        async with connection.cursor() as cursor:
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
                    logging.info(
                        f"Updating {tconst} with averageRating {averageRating} and numVotes {numVotes}."
                    )

                    # Execute the update query
                    await cursor.execute(update_sql, (averageRating, numVotes, tconst))

            await connection.commit()
            logging.info("Database updated successfully.")

    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        if connection:
            await database_pool.release_async_connection(connection)
            logging.info("Database connection released.")


# Path to your TSV file - ensure this path is correct
tsv_file_path = 'scripts/title.ratings.tsv'  # Update this path as necessary

async def main():
    await database_pool.init_pool()
    await read_tsv_and_update_database(tsv_file_path, database_pool)
    await database_pool.close_pool()

if __name__ == '__main__':
    asyncio.run(main())
