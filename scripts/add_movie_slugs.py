"""Add slug column and populate slugs for movies in title.basics.

Uses the application's DatabaseConnectionPool (with SSL) instead of
raw aiomysql connections with hardcoded credentials.

Usage:
    python add_movie_slugs.py
"""

import asyncio
import re

from logging_config import get_logger

logger = get_logger(__name__)


async def create_slug(title, year):
    title_slug = re.sub(r"[^\w\s-]", "", title).strip().lower()
    title_slug = re.sub(r"[-\s]+", "-", title_slug)
    if year:
        title_slug += f"-{year}"
    return title_slug


async def add_slug_column(db_pool):
    """Add the slug column if it doesn't exist."""
    result = await db_pool.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE table_name = 'title.basics' "
        "AND table_schema = DATABASE() "
        "AND column_name = 'slug'",
        fetch="one",
    )
    if not result:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "ALTER TABLE `title.basics` "
                    "ADD COLUMN `slug` VARCHAR(255) AFTER `primaryTitle`"
                )
        logger.info("Slug column added to 'title.basics' table.")


async def populate_slugs(db_pool, batch_size=500):
    """Generate and write slugs for movies that don't have one yet."""
    count_row = await db_pool.execute(
        "SELECT COUNT(*) AS cnt FROM `title.basics` "
        "WHERE `slug` IS NULL AND `titleType` = 'movie'",
        fetch="one",
    )
    total_remaining = count_row["cnt"] if count_row else 0
    logger.info("Found %d movies without slugs", total_remaining)

    if total_remaining == 0:
        logger.info("All movies already have slugs — nothing to do")
        return

    movies = (
        await db_pool.execute(
            "SELECT `tconst`, `primaryTitle`, `startYear` FROM `title.basics` "
            "WHERE `slug` IS NULL AND `titleType` = 'movie'",
            fetch="all",
        )
        or []
    )

    slugs = {}
    processed = 0

    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            batch = []
            for movie in movies:
                slug = await create_slug(movie["primaryTitle"], movie["startYear"])
                if slug in slugs:
                    slug += f"-{movie['startYear']}"
                slugs[slug] = True
                batch.append((f"film/{slug}", movie["tconst"]))
                processed += 1

                if len(batch) >= batch_size:
                    await cur.executemany(
                        "UPDATE `title.basics` "
                        "SET `slug` = %s WHERE `tconst` = %s AND `slug` IS NULL",
                        batch,
                    )
                    await conn.commit()
                    logger.info("Progress: %d/%d slugs added", processed, total_remaining)
                    batch = []

            # Final batch
            if batch:
                await cur.executemany(
                    "UPDATE `title.basics` "
                    "SET `slug` = %s WHERE `tconst` = %s AND `slug` IS NULL",
                    batch,
                )
                await conn.commit()

    logger.info("Completed: %d/%d slugs added", processed, total_remaining)


async def main():
    from settings import Config, DatabaseConnectionPool

    db_pool = DatabaseConnectionPool(Config.get_db_config())
    await db_pool.init_pool()
    try:
        await add_slug_column(db_pool)
        await populate_slugs(db_pool)
    except Exception as e:
        logger.error("An error occurred: %s", e)
    finally:
        await db_pool.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
