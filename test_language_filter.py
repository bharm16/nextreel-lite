#!/usr/bin/env python3
"""Test language filtering functionality."""

import asyncio
from scripts.filter_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from settings import Config, DatabaseConnectionPool
from scripts.movie import Movie

async def test_language_filtering():
    # Setup database connection
    db_config = Config.get_db_config()
    pool = DatabaseConnectionPool(db_config)
    await pool.init_pool()
    
    fetcher = ImdbRandomMovieFetcher(pool)
    
    print("Testing language filtering...")
    print("=" * 50)
    
    # Test 1: Any language
    print("\n1. Testing 'Any Language' filter:")
    criteria = {'language': 'any', 'min_year': 2024, 'max_year': 2025, 'min_votes': 1000, 'max_votes': 2000000}
    movies = await fetcher.fetch_random_movies(criteria, 3)
    if movies:
        for movie in movies[:3]:
            movie_obj = Movie(movie['tconst'], pool)
            movie_data = await movie_obj.get_movie_data()
            if movie_data:
                print(f"  - {movie_data.get('title')} ({movie_data.get('year')}) - Language: {movie_data.get('original_language', 'unknown')}")
    
    # Test 2: English only
    print("\n2. Testing 'English' filter:")
    criteria = {'language': 'en', 'min_year': 2024, 'max_year': 2025, 'min_votes': 1000, 'max_votes': 2000000}
    movies = await fetcher.fetch_random_movies(criteria, 3)
    if movies:
        for movie in movies[:3]:
            movie_obj = Movie(movie['tconst'], pool)
            movie_data = await movie_obj.get_movie_data()
            if movie_data:
                print(f"  - {movie_data.get('title')} ({movie_data.get('year')}) - Language: {movie_data.get('original_language', 'unknown')}")
    
    # Test 3: Spanish only
    print("\n3. Testing 'Spanish' filter:")
    criteria = {'language': 'es', 'min_year': 2020, 'max_year': 2025, 'min_votes': 1000, 'max_votes': 2000000}
    movies = await fetcher.fetch_random_movies(criteria, 3)
    if movies:
        for movie in movies[:3]:
            movie_obj = Movie(movie['tconst'], pool)
            movie_data = await movie_obj.get_movie_data()
            if movie_data:
                print(f"  - {movie_data.get('title')} ({movie_data.get('year')}) - Language: {movie_data.get('original_language', 'unknown')}")
    else:
        print("  No Spanish movies found with current criteria")
    
    # Test 4: Form data extraction
    print("\n4. Testing form data extraction:")
    
    class MockFormData:
        def __init__(self, data):
            self.data = data
        def get(self, key, default=None):
            return self.data.get(key, default)
        def getlist(self, key):
            return self.data.get(key, [])
    
    form_data = MockFormData({
        'language': 'fr',
        'year_min': '2024',
        'year_max': '2025',
        'imdb_score_min': '7.0',
        'genres[]': ['Drama', 'Comedy']
    })
    
    extracted = extract_movie_filter_criteria(form_data)
    print(f"  Extracted criteria: {extracted}")
    
    await pool.close_pool()
    print("\nTest completed!")

if __name__ == "__main__":
    asyncio.run(test_language_filtering())