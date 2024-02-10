import asyncio
import unittest
from config import Config
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher

class TestMovieFilteringWithRealDatabase(unittest.TestCase):
    def setUp(self):
        self.dbconfig = Config.STACKHERO_DB_CONFIG
        self.fetcher = ImdbRandomMovieFetcher(self.dbconfig)

    async def test_fetch_random_movies25_meets_criteria(self):
        """Test that fetch_random_movies25 fetches movies that meet the criteria."""
        criteria = {
            'min_year': 1990,
            'max_year': 2010,
            'min_rating': 7.0,
            'max_rating': 9.0,
            'min_votes': 10000,
            'title_type': 'movie',
            'language': 'en',
            'genres': ['Action', 'Drama']
        }
        movies = await self.fetcher.fetch_random_movies15(criteria)

        # Ensure that 25 movies are returned
        self.assertEqual(len(movies), 15, "Did not fetch exactly 25 movies")

        # Assert that returned movies match the criteria
        for movie in movies:
            self.assertTrue(1990 <= movie['startYear'] <= 2010, "Movie year out of range")
            self.assertTrue(7.0 <= movie['averageRating'] <= 9.0, "Movie rating out of range")
            self.assertTrue(movie['numVotes'] >= 10000, "Movie votes less than minimum")
            self.assertTrue(movie['titleType'] == 'movie', "Non-movie title type returned")
            self.assertTrue(movie['language'] == 'en', "Movie language does not match")
            # Add more assertions as necessary for genres, etc.

# Custom runner to handle async test cases
def async_test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestMovieFilteringWithRealDatabase))
    runner = unittest.TextTestRunner()
    loop.run_until_complete(runner.run(suite))

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    async_test_suite()
