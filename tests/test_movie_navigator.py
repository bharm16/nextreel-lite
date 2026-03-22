from quart import Quart, session
import pytest

from movie_navigator import MovieNavigator
from session_keys import WATCH_QUEUE_KEY


class CacheStub:
    def __init__(self, payload):
        self.payload = payload

    async def get(self, namespace, key):
        return self.payload

    async def set(self, namespace, key, value, ttl=None):
        return None


@pytest.mark.asyncio
async def test_get_movie_by_slug_resolves_queued_refs():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.secure_cache = CacheStub(
        {
            "imdb_id": "tt123",
            "slug": "my-movie",
            "title": "My Movie",
            "plot": "Resolved from cache",
        }
    )

    navigator = MovieNavigator(movie_fetcher=None, db_pool=None, tmdb_helper=None)

    async with app.app_context():
        async with app.test_request_context("/"):
            session[WATCH_QUEUE_KEY] = [
                {"imdb_id": "tt123", "slug": "my-movie", "title": "My Movie"}
            ]

            movie = await navigator.get_movie_by_slug("user-1", "my-movie")

            assert movie["plot"] == "Resolved from cache"
            assert movie["imdb_id"] == "tt123"
