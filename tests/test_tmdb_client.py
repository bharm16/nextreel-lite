import asyncio
from unittest.mock import AsyncMock, patch

from scripts.tmdb_client import TMDbHelper


def test_get_full_image_url():
    helper = TMDbHelper('key')
    url = helper.get_full_image_url('/path', size='w500')
    assert url == 'https://image.tmdb.org/t/p/w500/path'


def test_get_backdrop_image_for_home():
    async def run_test():
        helper = TMDbHelper('key')
        with patch.object(helper, '_get', AsyncMock(return_value={'backdrops': [{'file_path': '/b.jpg'}]})):
            url = await helper.get_backdrop_image_for_home(123)
            assert url.endswith('/b.jpg')

    asyncio.run(run_test())


def test_get_backdrop_for_movie():
    async def run_test():
        helper = TMDbHelper('key')
        helper.get_all_backdrop_images = AsyncMock(return_value=['url1', 'url2'])
        with patch('random.choice', lambda x: x[0]):
            url = await helper.get_backdrop_for_movie(123)
            assert url == 'url1'

    asyncio.run(run_test())
