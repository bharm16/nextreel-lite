#!/usr/bin/env python3
"""Check for recent movies in the database.

Uses the application's DatabaseConnectionPool (with SSL) instead of
raw aiomysql connections.

Usage:
    python check_2024_movies.py
"""

import asyncio


async def check_recent_movies():
    from infra.pool import DatabaseConnectionPool
    from settings import Config

    db_pool = DatabaseConnectionPool(Config.get_db_config())
    await db_pool.init_pool()

    try:
        print("Checking for recent movies in database...")

        # 1. Check year range
        result = await db_pool.execute(
            "SELECT MIN(startYear) as min_year, MAX(startYear) as max_year, "
            "COUNT(*) as total FROM `title.basics` WHERE titleType = 'movie'",
            fetch="one",
        )
        if result:
            print(
                f"\nYear range for movies: {result['min_year']} to "
                f"{result['max_year']} ({result['total']:,} total movies)"
            )

        # 2. Check recent years
        recent = (
            await db_pool.execute(
                "SELECT startYear, COUNT(*) as count FROM `title.basics` "
                "WHERE titleType = 'movie' AND startYear >= 2020 "
                "GROUP BY startYear ORDER BY startYear DESC",
                fetch="all",
            )
            or []
        )
        print("\nMovies by year (2020+):")
        for row in recent:
            print(f"  {row['startYear']}: {row['count']:,} movies")

        # 3. Check 2024 count
        result = await db_pool.execute(
            "SELECT COUNT(*) as count_2024 FROM `title.basics` "
            "WHERE titleType = 'movie' AND startYear = 2024",
            fetch="one",
        )
        if result:
            print(f"\nMovies with year 2024: {result['count_2024']}")

        # 4. Sample latest movies
        samples = (
            await db_pool.execute(
                "SELECT tconst, primaryTitle, startYear, titleType "
                "FROM `title.basics` WHERE titleType = 'movie' "
                "ORDER BY tconst DESC LIMIT 10",
                fetch="all",
            )
            or []
        )
        print("\nSample of latest movies (by tconst):")
        for movie in samples:
            print(f"  {movie['tconst']}: {movie['primaryTitle']} ({movie['startYear']})")

        # 5. Check data type of startYear
        col_info = await db_pool.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'title.basics' "
            "AND COLUMN_NAME = 'startYear'",
            fetch="one",
        )
        if col_info:
            print(f"\nstartYear column type: {col_info['DATA_TYPE']} / {col_info['COLUMN_TYPE']}")

    finally:
        await db_pool.close_pool()


if __name__ == "__main__":
    asyncio.run(check_recent_movies())
