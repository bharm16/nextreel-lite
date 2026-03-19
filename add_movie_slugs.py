import asyncio
import logging
from logging_config import get_logger
import os
import re
import aiomysql

# Assuming you have a .env file or environment variables set for DB configuration
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logger = get_logger(__name__)

# Database configurations from environment variables
DB_CONFIG = {
    'host': os.getenv('STACKHERO_DB_HOST'),
    'user': os.getenv('STACKHERO_DB_USER'),
    'password': os.getenv('STACKHERO_DB_PASSWORD'),
    'db': os.getenv('STACKHERO_DB_NAME'),  # 'db' is used by aiomysql
    'port': int(os.getenv('STACKHERO_DB_PORT', 3306)),
    'charset': 'utf8mb4',
    'autocommit': True
}

async def create_slug(title, year):
    title_slug = re.sub(r'[^\w\s-]', '', title).strip().lower()
    title_slug = re.sub(r'[-\s]+', '-', title_slug)
    if year:
        title_slug += f'-{year}'
    return title_slug

async def add_slug_column(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE table_name = 'title.basics'
                AND table_schema = 'imdb'
                AND column_name = 'slug';
            """)
            if not await cur.fetchone():
                await cur.execute("""
                    ALTER TABLE `title.basics`
                    ADD COLUMN `slug` VARCHAR(255) AFTER `primaryTitle`;
                """)
                logger.info("Slug column added to 'title.basics' table.")

async def populate_slugs(pool, batch_size=500):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # Only fetch movies that don't already have a slug (idempotent)
            await cur.execute("""
                SELECT COUNT(*) AS cnt FROM `title.basics`
                WHERE `slug` IS NULL AND `titleType` = 'movie';
            """)
            count_row = await cur.fetchone()
            total_remaining = count_row['cnt'] if count_row else 0
            logger.info(f"Found {total_remaining} movies without slugs")

            if total_remaining == 0:
                logger.info("All movies already have slugs — nothing to do")
                return

            await cur.execute("""
                SELECT `tconst`, `primaryTitle`, `startYear` FROM `title.basics`
                WHERE `slug` IS NULL AND `titleType` = 'movie';
            """)
            movies = await cur.fetchall()
            slugs = {}
            processed = 0
            batch = []
            for movie in movies:
                slug = await create_slug(movie['primaryTitle'], movie['startYear'])
                if slug in slugs:
                    slug += f"-{movie['startYear']}"
                slugs[slug] = True
                batch.append((f'film/{slug}', movie['tconst']))
                processed += 1

                if len(batch) >= batch_size:
                    await cur.executemany("""
                        UPDATE `title.basics`
                        SET `slug` = %s
                        WHERE `tconst` = %s AND `slug` IS NULL;
                    """, batch)
                    await conn.commit()
                    logger.info(f"Progress: {processed}/{total_remaining} slugs added")
                    batch = []

            # Final batch
            if batch:
                await cur.executemany("""
                    UPDATE `title.basics`
                    SET `slug` = %s
                    WHERE `tconst` = %s AND `slug` IS NULL;
                """, batch)
                await conn.commit()

            logger.info(f"Completed: {processed}/{total_remaining} slugs added")

async def main():
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
