"""Utility script to backfill URL-friendly slugs into the movies table.

The script connects to the MySQL database and for each movie without a slug
generates one based on the title and year.  Slugs make it easier to create
human readable URLs such as ``/movie/film/the-matrix-1999``.  Only comments are
added here to explain the workflow and no functional changes are made.
"""

import asyncio
import logging
from logging_config import get_logger
import os
import re
import aiomysql

from dotenv import load_dotenv

# Load environment variables (e.g. database credentials) from a ``.env`` file so
# the script can run locally without manually exporting them.
load_dotenv()

# Configure logging so progress messages are visible on the console.
logger = get_logger(__name__)

# Database configurations from environment variables
DB_CONFIG = {
    'host': os.getenv('STACKHERO_DB_HOST'),
    'user': os.getenv('STACKHERO_DB_USER'),
    'password': os.getenv('STACKHERO_DB_PASSWORD'),
    # ``aiomysql`` expects the database name under the key ``db``
    'db': os.getenv('STACKHERO_DB_NAME'),
    'port': int(os.getenv('STACKHERO_DB_PORT', 3306)),
    'charset': 'utf8mb4',
    'autocommit': True
}

async def create_slug(title, year):
    """Return a URL-friendly slug generated from ``title`` and ``year``."""

    title_slug = re.sub(r'[^\w\s-]', '', title).strip().lower()
    title_slug = re.sub(r'[-\s]+', '-', title_slug)
    if year:
        title_slug += f'-{year}'
    return title_slug

async def add_slug_column(pool):
    """Ensure the ``slug`` column exists on ``title.basics`` table."""

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE table_name = 'title.basics'
                AND table_schema = 'imdb'
                AND column_name = 'slug';
                """
            )
            if not await cur.fetchone():
                await cur.execute(
                    """
                    ALTER TABLE `title.basics`
                    ADD COLUMN `slug` VARCHAR(255) AFTER `primaryTitle`;
                    """
                )
                logger.info("Slug column added to 'title.basics' table.")

async def populate_slugs(pool):
    """Generate and store slugs for movies lacking one."""

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT `tconst`, `primaryTitle`, `startYear` FROM `title.basics`
                WHERE `slug` IS NULL AND `titleType` = 'movie';
                """
            )
            movies = await cur.fetchall()
            slugs = {}
            for movie in movies:
                slug = await create_slug(movie['primaryTitle'], movie['startYear'])
                if slug in slugs:
                    # Avoid collisions by appending the year again
                    slug += f"-{movie['startYear']}"
                slugs[slug] = True
                await cur.execute(
                    """
                    UPDATE `title.basics`
                    SET `slug` = %s
                    WHERE `tconst` = %s;
                    """,
                    (f'film/{slug}', movie['tconst']),
                )
                logger.info(
                    f"Slug '{slug}' added to movie with tconst: {movie['tconst']}"
                )

async def main():
    """Entry point used when running the script directly."""

    pool = await aiomysql.create_pool(**DB_CONFIG)
    try:
        await add_slug_column(pool)
        await populate_slugs(pool)
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        pool.close()
        await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
