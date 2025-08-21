#!/usr/bin/env python3
"""Check for 2024-2025 movies in the database."""

import asyncio
import aiomysql

async def check_recent_movies():
    conn = await aiomysql.connect(
        host='localhost',
        user='root', 
        password='caching_sha2_password',
        db='imdb',
        autocommit=True
    )
    
    cursor = await conn.cursor(aiomysql.DictCursor)
    
    print("Checking for 2024-2025 movies in database...")
    
    # 1. Check year range
    await cursor.execute("""
        SELECT 
            MIN(startYear) as min_year,
            MAX(startYear) as max_year,
            COUNT(*) as total
        FROM `title.basics`
        WHERE titleType = 'movie'
    """)
    result = await cursor.fetchone()
    print(f"\nYear range for movies: {result['min_year']} to {result['max_year']} ({result['total']:,} total movies)")
    
    # 2. Check recent years
    await cursor.execute("""
        SELECT 
            startYear,
            COUNT(*) as count
        FROM `title.basics`
        WHERE titleType = 'movie'
        AND startYear >= 2020
        GROUP BY startYear
        ORDER BY startYear DESC
    """)
    recent = await cursor.fetchall()
    print("\nMovies by year (2020+):")
    for row in recent:
        print(f"  {row['startYear']}: {row['count']:,} movies")
    
    # 3. Check if 2024 or 2025 exists as text
    await cursor.execute("""
        SELECT 
            COUNT(*) as count_2024
        FROM `title.basics`
        WHERE titleType = 'movie'
        AND (startYear = 2024 OR startYear = '2024')
    """)
    result = await cursor.fetchone()
    print(f"\nMovies with year 2024: {result['count_2024']}")
    
    # 4. Sample some recent movies
    await cursor.execute("""
        SELECT 
            tconst,
            primaryTitle,
            startYear,
            titleType
        FROM `title.basics`
        WHERE titleType = 'movie'
        ORDER BY tconst DESC
        LIMIT 10
    """)
    samples = await cursor.fetchall()
    print("\nSample of latest movies (by tconst):")
    for movie in samples:
        print(f"  {movie['tconst']}: {movie['primaryTitle']} ({movie['startYear']})")
    
    # 5. Check data type of startYear
    await cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'imdb'
        AND TABLE_NAME = 'title.basics'
        AND COLUMN_NAME = 'startYear'
    """)
    col_info = await cursor.fetchone()
    if col_info:
        print(f"\nstartYear column type: {col_info['DATA_TYPE']} / {col_info['COLUMN_TYPE']}")
    
    await cursor.close()
    conn.close()

if __name__ == "__main__":
    asyncio.run(check_recent_movies())