import csv
from logging_config import get_logger
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
logger = get_logger(__name__)


async def read_tsv_and_update_database(tsv_file_path, database_pool, batch_size=500):
    """
    Reads a TSV file and updates the averageRating and numVotes in the database.
    Uses batch execution for performance.

    :param tsv_file_path: Path to the TSV file containing the updates.
    :param database_pool: DatabaseConnectionPool instance for DB operations.
    :param batch_size: Number of rows per batch commit.
    """
    if not os.path.isfile(tsv_file_path):
        logger.error(f"TSV file not found: {tsv_file_path}")
        return

    connection = None
    try:
        connection = await database_pool.get_async_connection()
        logger.info("Successfully connected to database.")

        update_sql = """
        UPDATE `title.ratings`
        SET `averageRating` = %s, `numVotes` = %s
        WHERE `tconst` = %s
        """

        async with connection.cursor() as cursor:
            with open(tsv_file_path, 'r', encoding='utf-8') as tsvfile:
                reader = csv.DictReader(tsvfile, delimiter='\t')
                batch = []
                total = 0

                for row in reader:
                    batch.append((row['averageRating'], row['numVotes'], row['tconst']))

                    if len(batch) >= batch_size:
                        await cursor.executemany(update_sql, batch)
                        await connection.commit()
                        total += len(batch)
                        logger.info(f"Committed batch — {total} rows updated so far")
                        batch = []

                # Final batch
                if batch:
                    await cursor.executemany(update_sql, batch)
                    await connection.commit()
                    total += len(batch)

            logger.info(f"Database updated successfully. Total rows: {total}")

    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        if connection:
            await database_pool.release_async_connection(connection)
            logger.info("Database connection released.")


# Path to your TSV file - ensure this path is correct
tsv_file_path = 'scripts/title.ratings.tsv'  # Update this path as necessary

async def main():
    await database_pool.init_pool()
    await read_tsv_and_update_database(tsv_file_path, database_pool)
    await database_pool.close_pool()

if __name__ == '__main__':
    asyncio.run(main())
