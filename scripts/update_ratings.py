"""Batch-update title.ratings from a TSV file.

Uses the application's DatabaseConnectionPool (with SSL) rather than
raw aiomysql connections.

Usage:
    python -m scripts.update_ratings
"""

import csv
import os
import asyncio

from logging_config import get_logger

logger = get_logger(__name__)

# Resolve project root so the TSV path works regardless of cwd.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TSV_PATH = os.path.join(_PROJECT_ROOT, "scripts", "title.ratings.tsv")


async def read_tsv_and_update_database(tsv_file_path, db_pool, batch_size=500):
    """Read a TSV file and update averageRating/numVotes via the pool."""
    if not os.path.isfile(tsv_file_path):
        logger.error("TSV file not found: %s", tsv_file_path)
        return

    update_sql = """
    UPDATE `title.ratings`
    SET `averageRating` = %s, `numVotes` = %s
    WHERE `tconst` = %s
    """

    try:
        async with db_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                with open(tsv_file_path, "r", encoding="utf-8") as tsvfile:
                    reader = csv.DictReader(tsvfile, delimiter="\t")
                    batch = []
                    total = 0

                    for row in reader:
                        batch.append((row["averageRating"], row["numVotes"], row["tconst"]))

                        if len(batch) >= batch_size:
                            await cursor.executemany(update_sql, batch)
                            await connection.commit()
                            total += len(batch)
                            logger.info("Committed batch — %d rows updated so far", total)
                            batch = []

                    # Final batch
                    if batch:
                        await cursor.executemany(update_sql, batch)
                        await connection.commit()
                        total += len(batch)

                logger.info("Database updated successfully. Total rows: %d", total)
    except Exception as e:
        logger.error("An error occurred: %s", e)


async def main():
    from settings import Config, DatabaseConnectionPool

    db_pool = DatabaseConnectionPool(Config.get_db_config())
    await db_pool.init_pool()
    try:
        await read_tsv_and_update_database(DEFAULT_TSV_PATH, db_pool)
    finally:
        await db_pool.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
