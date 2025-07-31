import csv
import logging
import os
import asyncio

from config import Config, DatabaseConnectionPool

dbconfig = Config.get_db_config()

# Use os.path.dirname to go up one level from the current script's directory
# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def read_tsv_and_update_database(tsv_file_path, db_pool):
    """Read a TSV file and update ratings in the database asynchronously."""
    connection = await db_pool.get_async_connection()
    if not connection:
        logging.error("Failed to acquire database connection.")
        return

    try:
        async with connection.cursor() as cursor:
            with open(tsv_file_path, "r", encoding="utf-8") as tsvfile:
                reader = csv.DictReader(tsvfile, delimiter="\t")
                for row in reader:
                    tconst = row["tconst"]
                    averageRating = row["averageRating"]
                    numVotes = row["numVotes"]

                    update_sql = """
                    UPDATE `title.ratings`
                    SET `averageRating` = %s, `numVotes` = %s
                    WHERE `tconst` = %s
                    """

                    logging.info(
                        f"Updating {tconst} with averageRating {averageRating} and numVotes {numVotes}."
                    )
                    await cursor.execute(update_sql, (averageRating, numVotes, tconst))

            await connection.commit()
            logging.info("Database updated successfully.")
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        await db_pool.release_async_connection(connection)
        logging.info("Database connection released.")


# Path to your TSV file - ensure this path is correct
tsv_file_path = "scripts/title.ratings.tsv"  # Update this path as necessary


async def main() -> None:
    db_pool = DatabaseConnectionPool(dbconfig)
    await db_pool.init_pool()
    await read_tsv_and_update_database(tsv_file_path, db_pool)
    await db_pool.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
