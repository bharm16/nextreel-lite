#!/usr/bin/env python3
"""Test the actual query that's timing out."""

import asyncio
import aiomysql
import time

async def test_problem_query():
    conn = await aiomysql.connect(
        host='localhost',
        user='root', 
        password='caching_sha2_password',
        db='imdb',
        autocommit=True
    )
    
    cursor = await conn.cursor(aiomysql.DictCursor)
    
    print("Testing the problematic query...")
    
    # The exact query from filter_backend.py
    query = """
    SELECT tb.* 
    FROM `title.basics` tb 
    JOIN `title.ratings` tr ON tb.tconst = tr.tconst 
    WHERE tb.startYear BETWEEN %s AND %s 
    AND tr.averagerating BETWEEN %s AND %s 
    AND tr.numVotes >= %s AND tr.numVotes <= %s 
    AND tb.titleType = %s 
    AND tb.language LIKE %s
    ORDER BY RAND() 
    LIMIT 2
    """
    
    params = [2024, 2025, 7.0, 10.0, 1000, 2000000, 'movie', '%en%']
    
    print(f"Query parameters: {params}")
    
    start = time.time()
    try:
        await cursor.execute(query, params)
        results = await cursor.fetchall()
        elapsed = time.time() - start
        
        print(f"\nQuery completed in {elapsed:.2f} seconds")
        print(f"Found {len(results)} movies")
        
        if results:
            for movie in results[:3]:
                print(f"  - {movie.get('primaryTitle')} ({movie.get('startYear')})")
    except Exception as e:
        elapsed = time.time() - start
        print(f"\nQuery failed after {elapsed:.2f} seconds")
        print(f"Error: {e}")
    
    # Try without language filter
    print("\n\nTrying without language filter...")
    query2 = """
    SELECT tb.* 
    FROM `title.basics` tb 
    JOIN `title.ratings` tr ON tb.tconst = tr.tconst 
    WHERE tb.startYear BETWEEN %s AND %s 
    AND tr.averagerating BETWEEN %s AND %s 
    AND tr.numVotes >= %s AND tr.numVotes <= %s 
    AND tb.titleType = %s 
    ORDER BY RAND() 
    LIMIT 2
    """
    
    params2 = [2024, 2025, 7.0, 10.0, 1000, 2000000, 'movie']
    
    start = time.time()
    try:
        await cursor.execute(query2, params2)
        results = await cursor.fetchall()
        elapsed = time.time() - start
        
        print(f"Query completed in {elapsed:.2f} seconds")
        print(f"Found {len(results)} movies")
        
        if results:
            for movie in results[:3]:
                print(f"  - {movie.get('primaryTitle')} ({movie.get('startYear')})")
    except Exception as e:
        elapsed = time.time() - start
        print(f"Query failed after {elapsed:.2f} seconds")
        print(f"Error: {e}")
    
    # Check how many 2024-2025 movies have ratings
    print("\n\nChecking 2024-2025 movies with ratings...")
    await cursor.execute("""
        SELECT 
            tb.startYear,
            COUNT(*) as total,
            COUNT(tr.tconst) as with_ratings,
            AVG(tr.numVotes) as avg_votes
        FROM `title.basics` tb
        LEFT JOIN `title.ratings` tr ON tb.tconst = tr.tconst
        WHERE tb.startYear BETWEEN 2024 AND 2025
        AND tb.titleType = 'movie'
        GROUP BY tb.startYear
    """)
    
    stats = await cursor.fetchall()
    for stat in stats:
        print(f"  {stat['startYear']}: {stat['total']} total, {stat['with_ratings']} with ratings, avg votes: {stat['avg_votes']:.0f if stat['avg_votes'] else 0}")
    
    await cursor.close()
    conn.close()

if __name__ == "__main__":
    asyncio.run(test_problem_query())