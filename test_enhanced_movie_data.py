#!/usr/bin/env python3
"""Test the enhanced movie data with age rating, budget, etc."""

import asyncio
from scripts.movie import Movie
from settings import Config, DatabaseConnectionPool

async def test_enhanced_movie_data():
    # Setup database connection
    db_config = Config.get_db_config()
    pool = DatabaseConnectionPool(db_config)
    await pool.init_pool()
    
    # Test with a few different movies
    test_movies = [
        "tt0111161",  # The Shawshank Redemption
        "tt0068646",  # The Godfather  
        "tt0468569",  # The Dark Knight
    ]
    
    print("Testing enhanced movie data...")
    print("=" * 80)
    
    for tconst in test_movies:
        print(f"\nTesting movie: {tconst}")
        print("-" * 40)
        
        movie = Movie(tconst, pool)
        movie_data = await movie.get_movie_data()
        
        if movie_data:
            print(f"Title: {movie_data.get('title')}")
            print(f"Year: {movie_data.get('year')}")
            print(f"Age Rating: {movie_data.get('age_rating', 'N/A')}")
            print(f"Runtime: {movie_data.get('runtime', 'N/A')}")
            print(f"Language: {movie_data.get('original_language', 'N/A')}")
            print(f"Budget: {movie_data.get('budget', 'N/A')}")
            print(f"Revenue: {movie_data.get('revenue', 'N/A')}")
            print(f"Countries: {movie_data.get('production_countries', 'N/A')}")
            print(f"Status: {movie_data.get('status', 'N/A')}")
            print(f"Tagline: {movie_data.get('tagline', 'N/A')}")
        else:
            print("Failed to fetch movie data")
    
    await pool.close_pool()
    print("\n" + "=" * 80)
    print("Test completed!")

if __name__ == "__main__":
    asyncio.run(test_enhanced_movie_data())