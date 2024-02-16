import asyncio
import re
import aiomysql
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'caching_sha2_password',
    'db': 'imdb',
}

async def create_slug(title, year):
    # Create URL-friendly slugs
    title_slug = re.sub(r'[^\w\s-]', '', title).strip().lower()
    title_slug = re.sub(r'[-\s]+', '-', title_slug)
    if year:
        title_slug += f'-{year}'
    return title_slug

async def add_slug_column(pool):
    # Check if slug column exists, if not then add it
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE table_name = 'title.basics'
                AND table_schema = 'imdb'
                AND column_name = 'slug';
            """)
            result = await cur.fetchone()
            if not result:
                await cur.execute("""
                    ALTER TABLE `title.basics`
                    ADD COLUMN `slug` VARCHAR(255) AFTER `primaryTitle`;
                """)
                logging.info("Slug column added to 'title.basics' table.")
                await conn.commit()

async def populate_slugs(pool):
    # Select movies without a slug
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("""
                SELECT `tconst`, `primaryTitle`, `startYear` FROM `title.basics`
                WHERE `slug` IS NULL AND `titleType` = 'movie';
            """)
            movies = await cur.fetchall()

            slugs = {}  # Keep track of existing slugs to detect duplicates
            # Update the slug for each movie
            for movie in movies:
                slug = await create_slug(movie['primaryTitle'], movie['startYear'])
                # Check if slug already exists to append the year
                if slug in slugs:
                    slug = await create_slug(movie['primaryTitle'], movie['startYear'])
                slugs[slug] = True

                await cur.execute("""
                    UPDATE `title.basics`
                    SET `slug` = %s
                    WHERE `tconst` = %s;
                """, (f'film/{slug}', movie['tconst']))
                logging.info(f"Slug '{slug}' added to movie with tconst: {movie['tconst']}")
            await conn.commit()

async def main():
    # Database connection pool
    pool = await aiomysql.create_pool(
        host=db_config['host'],
        port=3306,  # default MySQL port
        user=db_config['user'],
        password=db_config['password'],
        db=db_config['db'],
        charset='utf8mb4',
        autocommit=True
    )

    try:
        # Add slug column
        await add_slug_column(pool)

        # Populate the slug column
        await populate_slugs(pool)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        # Close the pool
        pool.close()
        await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
