from unittest.mock import AsyncMock, patch

from movies.tmdb_client import TMDbHelper


def test_get_full_image_url():
    helper = TMDbHelper('key')
    url = helper.get_full_image_url('/path', size='w500')
    assert url == 'https://image.tmdb.org/t/p/w500/path'


def test_build_request_options_uses_query_param_for_v3_api_key():
    helper = TMDbHelper("1234567890abcdef1234567890abcdef")

    headers, params = helper._build_request_options({"language": "en-US"})

    assert headers == {}
    assert params == {
        "language": "en-US",
        "api_key": "1234567890abcdef1234567890abcdef",
    }


def test_build_request_options_uses_bearer_header_for_v4_token():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ0bWRiIn0.signature"
    helper = TMDbHelper(token)

    headers, params = helper._build_request_options({"language": "en-US"})

    assert headers == {"Authorization": f"Bearer {token}"}
    assert params == {"language": "en-US"}


async def test_get_backdrop_image_for_home():
    helper = TMDbHelper('key')
    with patch.object(helper, '_get', AsyncMock(return_value={'backdrops': [{'file_path': '/b.jpg'}]})):
        url = await helper.get_backdrop_image_for_home(123)
        assert url.endswith('/b.jpg')


async def test_get_backdrop_for_movie():
    helper = TMDbHelper('key')
    helper.get_all_backdrop_images = AsyncMock(return_value=['url1', 'url2'])
    with patch('random.choice', lambda x: x[0]):
        url = await helper.get_backdrop_for_movie(123)
        assert url == 'url1'
