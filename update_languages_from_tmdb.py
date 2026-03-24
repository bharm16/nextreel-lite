#!/usr/bin/env python3
"""Update NULL language values in the database using TMDB data.

Uses the application's DatabaseConnectionPool (with SSL) instead of
raw aiomysql connections.

Usage:
    python update_languages_from_tmdb.py
"""

import asyncio

from scripts.tmdb_client import TMDbHelper
from logging_config import get_logger

logger = get_logger(__name__)


async def update_languages():
    from settings import Config, DatabaseConnectionPool

    db_pool = DatabaseConnectionPool(Config.get_db_config())
    await db_pool.init_pool()

    tmdb = TMDbHelper()

    print("Finding movies with NULL language values...")

    try:
        # Get movies with NULL language that have ratings
        select_query = (
            "SELECT tb.tconst, tb.primaryTitle, tb.startYear "
            "FROM `title.basics` tb "
            "JOIN `title.ratings` tr ON tb.tconst = tr.tconst "
            "WHERE tb.language IS NULL "
            "AND tb.titleType = 'movie' "
            "AND tb.startYear >= 2020 "
            "AND tr.numVotes >= 1000 "
            "ORDER BY tr.numVotes DESC "
            "LIMIT 5000"
        )
        movies = await db_pool.execute(select_query, fetch="all") or []
        print(f"Found {len(movies)} movies to update")

        updated = 0
        failed = 0

        for movie in movies:
            try:
                tmdb_id = await tmdb.get_tmdb_id_by_tconst(movie["tconst"])
                if not tmdb_id:
                    continue

                movie_info = await tmdb.get_movie_info_by_tmdb_id(tmdb_id)
                if not movie_info:
                    continue

                language = movie_info.get("original_language")
                if language:
                    await db_pool.execute(
                        "UPDATE `title.basics` SET language = %s WHERE tconst = %s",
                        [language, movie["tconst"]],
                        fetch="rowcount",
                    )
                    updated += 1

                    if updated % 100 == 0:
                        logger.info("Updated %d movies so far...", updated)

            except Exception as e:
                logger.error("Error updating %s: %s", movie["tconst"], e)
                failed += 1

            # Rate limiting — TMDB allows 40 requests per 10 seconds
            await asyncio.sleep(0.3)

        print(f"\nCompleted!")
        print(f"Successfully updated: {updated} movies")
        print(f"Failed: {failed} movies")

        # Show updated language distribution
        dist_query = (
            "SELECT COALESCE(language, 'NULL') as lang, COUNT(*) as count "
            "FROM `title.basics` "
            "WHERE titleType = 'movie' AND startYear >= 2020 "
            "GROUP BY language ORDER BY count DESC LIMIT 10"
        )
        results = await db_pool.execute(dist_query, fetch="all") or []
        print("\nUpdated language distribution for recent movies:")
        for r in results:
            print(f"  {r['lang']}: {r['count']} movies")

    finally:
        await tmdb.close()
        await db_pool.close_pool()


if __name__ == "__main__":
    print("=" * 60)
    print("TMDB Language Update Script")
    print("=" * 60)
    print("\nThis script will update NULL language values using TMDB data.")
    print("It's optional but will improve query performance.\n")

    response = input("Do you want to proceed? (y/n): ")
    if response.lower() == "y":
        asyncio.run(update_languages())
    else:
        print("Cancelled.")
