"""Extended tests for MovieNavigator — navigation stacks, queue management."""

from unittest.mock import AsyncMock, patch

import pytest
from quart import Quart, session

from movie_navigator import (
    MovieNavigator,
    _movie_ref,
    _is_full_movie,
    MAX_PREV_STACK_SIZE,
)
from session.keys import (
    PREVIOUS_STACK_KEY,
    FUTURE_STACK_KEY,
    SEEN_TCONSTS_KEY,
    WATCH_QUEUE_KEY,
    CURRENT_MOVIE_KEY,
    CRITERIA_KEY,
)


# ---------------------------------------------------------------------------
# Helper unit functions
# ---------------------------------------------------------------------------


class TestMovieRef:
    """_movie_ref extracts lightweight references."""

    def test_extracts_correct_keys(self):
        full = {
            "imdb_id": "tt123",
            "tmdb_id": 456,
            "title": "Test Movie",
            "slug": "test-movie",
            "plot": "A long plot description",
            "cast": ["Actor A"],
            "credits": {},
        }
        ref = _movie_ref(full)
        assert set(ref.keys()) == {"imdb_id", "tmdb_id", "title", "slug"}
        assert ref["imdb_id"] == "tt123"
        assert "plot" not in ref
        assert "cast" not in ref

    def test_handles_missing_keys(self):
        ref = _movie_ref({"imdb_id": "tt999"})
        assert ref["imdb_id"] == "tt999"
        assert ref["tmdb_id"] is None
        assert ref["title"] is None
        assert ref["slug"] is None


class TestIsFullMovie:
    def test_with_full_sentinel(self):
        assert _is_full_movie({"_full": True, "cast": ["Actor"]}) is True

    def test_without_full_sentinel(self):
        # Legacy dicts without _full are treated as lightweight refs
        assert _is_full_movie({"cast": ["Actor"]}) is False
        assert _is_full_movie({"credits": {}}) is False
        assert _is_full_movie({"plot": "desc"}) is False

    def test_ref_only(self):
        assert _is_full_movie({"imdb_id": "tt123", "title": "T"}) is False


# ---------------------------------------------------------------------------
# Session stack management
# ---------------------------------------------------------------------------


class CacheStub:
    def __init__(self, data=None):
        self._store = {}
        self.data = data

    async def get(self, namespace, key):
        if self.data:
            return self.data
        return self._store.get(f"{namespace}:{key}")

    async def set(self, namespace, key, value, ttl=None):
        self._store[f"{namespace}:{key}"] = value


class FetcherStub:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_random_movies = AsyncMock(return_value=rows)


def _full_movie(tconst: str, title: str | None = None) -> dict:
    label = title or tconst
    return {
        "imdb_id": tconst,
        "tmdb_id": int(tconst.replace("tt", "") or 0),
        "title": label,
        "slug": label.lower().replace(" ", "-"),
        "backdrop_url": f"https://example.com/{tconst}.jpg",
        "original_language": "en",
        "spoken_languages": ["en"],
        "_full": True,
    }


@pytest.fixture
def nav_app():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.secure_cache = CacheStub()
    # Register the routes blueprint so url_for("main.movie_detail") works
    from routes import bp
    app.register_blueprint(bp)
    return app


class TestMarkMovieSeen:
    @pytest.mark.asyncio
    async def test_marks_tconst_seen(self, nav_app):
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                session[SEEN_TCONSTS_KEY] = []
                nav._mark_movie_seen("tt111")
                assert "tt111" in session[SEEN_TCONSTS_KEY]

    @pytest.mark.asyncio
    async def test_does_not_duplicate(self, nav_app):
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                session[SEEN_TCONSTS_KEY] = ["tt111"]
                nav._mark_movie_seen("tt111")
                assert session[SEEN_TCONSTS_KEY].count("tt111") == 1

    @pytest.mark.asyncio
    async def test_caps_seen_list(self, nav_app):
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                # Fill beyond 2x MAX_PREV_STACK_SIZE
                big_list = [f"tt{i}" for i in range(MAX_PREV_STACK_SIZE * 2 + 10)]
                session[SEEN_TCONSTS_KEY] = big_list
                nav._mark_movie_seen("tt_new")
                assert len(session[SEEN_TCONSTS_KEY]) <= MAX_PREV_STACK_SIZE * 2 + 1


class TestGetUserStacks:
    @pytest.mark.asyncio
    async def test_initializes_empty_stacks(self, nav_app):
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                prev, future = nav.get_user_stacks()
                assert prev == []
                assert future == []
                assert PREVIOUS_STACK_KEY in session
                assert FUTURE_STACK_KEY in session

    @pytest.mark.asyncio
    async def test_returns_existing_stacks(self, nav_app):
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                session[PREVIOUS_STACK_KEY] = [{"imdb_id": "tt1"}]
                session[FUTURE_STACK_KEY] = [{"imdb_id": "tt2"}]
                prev, future = nav.get_user_stacks()
                assert len(prev) == 1
                assert len(future) == 1


class TestPreviousMovie:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_history(self, nav_app):
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                session[PREVIOUS_STACK_KEY] = []
                session[FUTURE_STACK_KEY] = []
                result = await nav.previous_movie("user-1")
                assert result is None

    @pytest.mark.asyncio
    async def test_pops_from_prev_stack(self, nav_app):
        """Going back pops from prev_stack and pushes current to future_stack."""
        full_data = {
            "imdb_id": "tt_prev",
            "tmdb_id": 1,
            "title": "Previous",
            "slug": "previous",
            "plot": "Full data",
        }
        nav_app.secure_cache = CacheStub(data=full_data)
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                current = {"imdb_id": "tt_current", "title": "Current"}
                session[CURRENT_MOVIE_KEY] = current
                session[PREVIOUS_STACK_KEY] = [
                    {"imdb_id": "tt_prev", "slug": "previous", "title": "Previous", "tmdb_id": 1}
                ]
                session[FUTURE_STACK_KEY] = []

                result = await nav.previous_movie("user-1")
                # Current movie should now be in future stack (as a ref)
                assert len(session[FUTURE_STACK_KEY]) == 1
                assert session[FUTURE_STACK_KEY][0]["imdb_id"] == "tt_current"
                # Prev stack should be empty now
                assert session[PREVIOUS_STACK_KEY] == []


class TestQueueLoading:
    @pytest.mark.asyncio
    async def test_load_movies_into_queue_skips_current_and_seen_movies(self, nav_app):
        fetcher = FetcherStub(
            [{"tconst": "tt1"}, {"tconst": "tt2"}, {"tconst": "tt3"}]
        )
        nav = MovieNavigator(movie_fetcher=fetcher, db_pool=None)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                session[CRITERIA_KEY] = {"language": "en"}
                session[WATCH_QUEUE_KEY] = []
                session[CURRENT_MOVIE_KEY] = {"imdb_id": "tt1"}
                session[SEEN_TCONSTS_KEY] = ["tt2"]

                with patch(
                    "movies.movie.Movie.get_movie_data",
                    AsyncMock(
                        side_effect=[
                            _full_movie("tt1"),
                            _full_movie("tt2"),
                            _full_movie("tt3"),
                        ]
                    ),
                ):
                    await nav._load_movies_into_queue()

                assert [ref["imdb_id"] for ref in session[WATCH_QUEUE_KEY]] == ["tt3"]


class TestNextMovie:
    @pytest.mark.asyncio
    async def test_next_movie_consumes_existing_queue_before_refetching(self, nav_app):
        fetcher = FetcherStub([])
        nav = MovieNavigator(movie_fetcher=fetcher, db_pool=None)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                session[WATCH_QUEUE_KEY] = [
                    _movie_ref(_full_movie("tt1")),
                    _movie_ref(_full_movie("tt2")),
                ]
                session[PREVIOUS_STACK_KEY] = []
                session[FUTURE_STACK_KEY] = []
                session[SEEN_TCONSTS_KEY] = []
                session[CURRENT_MOVIE_KEY] = None

                with patch(
                    "movie_navigator._resolve_ref",
                    AsyncMock(side_effect=[_full_movie("tt1"), _full_movie("tt2")]),
                ):
                    first = await nav.next_movie("user-1")
                    second = await nav.next_movie("user-1")

                assert first.location.endswith("/movie/tt1")
                assert second.location.endswith("/movie/tt2")
                assert session[CURRENT_MOVIE_KEY]["imdb_id"] == "tt2"
                assert session[WATCH_QUEUE_KEY] == []
                fetcher.fetch_random_movies.assert_not_awaited()


class TestGetCurrentMovieTconst:
    @pytest.mark.asyncio
    async def test_returns_tconst_when_set(self, nav_app):
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                session[CURRENT_MOVIE_KEY] = {"imdb_id": "tt999"}
                assert nav.get_current_movie_tconst() == "tt999"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_set(self, nav_app):
        nav = MovieNavigator(movie_fetcher=None, db_pool=None)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                assert nav.get_current_movie_tconst() is None



# TestGetMovieBySlug removed — get_movie_by_slug was deleted from
# MovieNavigator. See movie_navigator.py for rationale.
