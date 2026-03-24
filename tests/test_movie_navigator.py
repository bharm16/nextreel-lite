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


# get_movie_by_slug was removed from MovieNavigator — tests deleted.
# See movie_navigator.py for rationale.
