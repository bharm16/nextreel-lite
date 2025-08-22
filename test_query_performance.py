#!/usr/bin/env python3
"""Test query performance after indexing."""

import asyncio
import time
import aiomysql
from typing import Dict, Any

async def test_query_performance():
    """Test the main application queries and measure performance."""
    
    # Database connection parameters
    db_config = {
        'host': 'localhost',
        'user': 'root',
        'password': 'caching_sha2_password',
        'db': 'imdb',
        'autocommit': True
    }
    
    # Connect to database
    conn = await aiomysql.connect(**db_config)
    cursor = await conn.cursor(aiomysql.DictCursor)
    
    print("=" * 60)
    print("QUERY PERFORMANCE TEST RESULTS")
    print("=" * 60)
    
    # Test 1: Main filter query (from filter_backend.py)
    print("\n1. Main Movie Filter Query (with JOIN)")
    query1 = """
    SELECT SQL_NO_CACHE tb.* 
    FROM `title.basics` tb 
    JOIN `title.ratings` tr ON tb.tconst = tr.tconst
    WHERE tb.startYear BETWEEN %s AND %s
    AND tr.averageRating BETWEEN %s AND %s
    AND tr.numVotes >= %s AND tr.numVotes <= %s
    AND tb.titleType = %s
    AND tb.language LIKE %s
    ORDER BY RAND() 
    LIMIT 15
    """
    params1 = [2000, 2023, 7.0, 10.0, 100000, 1000000, 'movie', '%en%']
    
    start_time = time.time()
    await cursor.execute(query1, params1)
    results1 = await cursor.fetchall()
    elapsed1 = time.time() - start_time
    
    print(f"   Execution time: {elapsed1:.3f} seconds")
    print(f"   Results found: {len(results1)} movies")
    
    # Test 2: Slug lookup query (from movie.py)
    print("\n2. Slug Lookup Query")
    query2 = """
    SELECT SQL_NO_CACHE slug 
    FROM `title.basics` 
    WHERE tconst = %s
    """
    params2 = ['tt0111161']  # Example tconst
    
    start_time = time.time()
    await cursor.execute(query2, params2)
    result2 = await cursor.fetchone()
    elapsed2 = time.time() - start_time
    
    print(f"   Execution time: {elapsed2:.3f} seconds")
    print(f"   Found: {result2 is not None}")
    
    # Test 3: Ratings lookup query
    print("\n3. Ratings Lookup Query")
    query3 = """
    SELECT SQL_NO_CACHE tr.tconst, tr.averageRating, tr.numVotes
    FROM `title.ratings` tr
    WHERE tr.tconst = %s
    """
    params3 = ['tt0111161']
    
    start_time = time.time()
    await cursor.execute(query3, params3)
    result3 = await cursor.fetchone()
    elapsed3 = time.time() - start_time
    
    print(f"   Execution time: {elapsed3:.3f} seconds")
    print(f"   Rating: {result3['averageRating'] if result3 else 'N/A'}")
    
    # Test 4: Genre filter query
    print("\n4. Genre Filter Query (Action + Comedy)")
    query4 = """
    SELECT SQL_NO_CACHE COUNT(*) as count
    FROM `title.basics` tb 
    JOIN `title.ratings` tr ON tb.tconst = tr.tconst
    WHERE tb.startYear BETWEEN %s AND %s
    AND tr.averageRating >= %s
    AND tb.titleType = %s
    AND (tb.genres LIKE %s OR tb.genres LIKE %s)
    """
    params4 = [2010, 2023, 7.0, 'movie', '%Action%', '%Comedy%']
    
    start_time = time.time()
    await cursor.execute(query4, params4)
    result4 = await cursor.fetchone()
    elapsed4 = time.time() - start_time
    
    print(f"   Execution time: {elapsed4:.3f} seconds")
    print(f"   Movies found: {result4['count']}")
    
    # Test 5: High-rated recent movies
    print("\n5. High-Rated Recent Movies Query")
    query5 = """
    SELECT SQL_NO_CACHE tb.tconst, tb.primaryTitle, tr.averageRating, tr.numVotes
    FROM `title.basics` tb
    JOIN `title.ratings` tr ON tb.tconst = tr.tconst
    WHERE tb.startYear >= %s
    AND tr.averageRating >= %s
    AND tr.numVotes >= %s
    AND tb.titleType = %s
    ORDER BY tr.averageRating DESC, tr.numVotes DESC
    LIMIT 10
    """
    params5 = [2020, 8.0, 50000, 'movie']
    
    start_time = time.time()
    await cursor.execute(query5, params5)
    results5 = await cursor.fetchall()
    elapsed5 = time.time() - start_time
    
    print(f"   Execution time: {elapsed5:.3f} seconds")
    print(f"   Top movie: {results5[0]['primaryTitle'] if results5 else 'None'}")
    
    # Summary
    print("\n" + "=" * 60)
    print("PERFORMANCE SUMMARY")
    print("=" * 60)
    total_time = elapsed1 + elapsed2 + elapsed3 + elapsed4 + elapsed5
    print(f"Total execution time for all queries: {total_time:.3f} seconds")
    print(f"Average query time: {total_time/5:.3f} seconds")
    
    # Performance recommendations
    print("\n" + "=" * 60)
    print("PERFORMANCE ANALYSIS")
    print("=" * 60)
    
    if elapsed1 > 1.0:
        print("⚠️  Main filter query is slow (>1s)")
        print("   Consider:")
        print("   - Creating a materialized view for common filters")
        print("   - Using a smaller active movies subset table")
        print("   - Implementing query result caching")
    else:
        print("✅ Main filter query performance is good")
    
    if elapsed2 > 0.01:
        print("⚠️  Slug lookup could be optimized")
        print("   Consider adding a covering index on (tconst, slug)")
    else:
        print("✅ Slug lookup is fast")
    
    if elapsed3 > 0.01:
        print("⚠️  Ratings lookup could be optimized")
    else:
        print("✅ Ratings lookup is fast")
    
    # Close connection
    await cursor.close()
    conn.close()

if __name__ == "__main__":
    asyncio.run(test_query_performance())