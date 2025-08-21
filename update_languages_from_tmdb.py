#!/usr/bin/env python3
"""
Update NULL language values in the database using TMDB data.
This is optional but will improve query performance.
"""

import asyncio
import aiomysql
from scripts.tmdb_client import TMDbHelper
import logging
from tqdm import tqdm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def update_languages():
    # Database connection
    conn = await aiomysql.connect(
        host='localhost',
        user='root',
        password='caching_sha2_password',
        db='imdb',
        autocommit=False
    )
    cursor = await conn.cursor(aiomysql.DictCursor)
    
    # TMDB helper
    tmdb = TMDbHelper()
    
    print("Finding movies with NULL language values...")
    
    # Get movies with NULL language that have ratings (more likely to have TMDB data)
    await cursor.execute("""
        SELECT tb.tconst, tb.primaryTitle, tb.startYear
        FROM `title.basics` tb
        JOIN `title.ratings` tr ON tb.tconst = tr.tconst
        WHERE tb.language IS NULL
        AND tb.titleType = 'movie'
        AND tb.startYear >= 2020
        AND tr.numVotes >= 1000
        ORDER BY tr.numVotes DESC
        LIMIT 5000
    """)
    
    movies = await cursor.fetchall()
    print(f"Found {len(movies)} movies to update")
    
    updated = 0
    failed = 0
    
    for movie in tqdm(movies, desc="Updating languages"):
        try:
            # Get TMDB ID
            tmdb_id = await tmdb.get_tmdb_id_by_tconst(movie['tconst'])
            if not tmdb_id:
                continue
            
            # Get movie info from TMDB
            movie_info = await tmdb.get_movie_info_by_tmdb_id(tmdb_id)
            if not movie_info:
                continue
            
            # Get language
            language = movie_info.get('original_language')
            if language:
                # Update database
                await cursor.execute("""
                    UPDATE `title.basics`
                    SET language = %s
                    WHERE tconst = %s
                """, (language, movie['tconst']))
                
                updated += 1
                
                # Commit every 100 updates
                if updated % 100 == 0:
                    await conn.commit()
                    logger.info(f"Updated {updated} movies so far...")
                    
        except Exception as e:
            logger.error(f"Error updating {movie['tconst']}: {e}")
            failed += 1
            
        # Rate limiting - TMDB allows 40 requests per 10 seconds
        await asyncio.sleep(0.3)
    
    # Final commit
    await conn.commit()
    
    print(f"\nCompleted!")
    print(f"Successfully updated: {updated} movies")
    print(f"Failed: {failed} movies")
    
    # Show updated language distribution
    await cursor.execute("""
        SELECT 
            COALESCE(language, 'NULL') as lang,
            COUNT(*) as count
        FROM `title.basics`
        WHERE titleType = 'movie'
        AND startYear >= 2020
        GROUP BY language
        ORDER BY count DESC
        LIMIT 10
    """)
    
    print("\nUpdated language distribution for recent movies:")
    results = await cursor.fetchall()
    for r in results:
        print(f"  {r['lang']}: {r['count']} movies")
    
    await cursor.close()
    conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("TMDB Language Update Script")
    print("=" * 60)
    print("\nThis script will update NULL language values using TMDB data.")
    print("It's optional but will improve query performance.\n")
    
    response = input("Do you want to proceed? (y/n): ")
    if response.lower() == 'y':
        asyncio.run(update_languages())
    else:
        print("Cancelled.")