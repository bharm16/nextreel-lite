"""Extended MovieManager tests — navigation delegation and error paths."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from infra.navigation_state import NavigationState, default_filter_state, utcnow
from movie_service import MovieManager
from tests.helpers import TEST_ENV


def _state(**overrides) -> NavigationState:
    now = utcnow()
    defaults = dict(
        session_id="state-1",
        version=1,
        csrf_token="csrf",
        filters=default_filter_state(),
        current_tconst=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
    )
    defaults.update(overrides)
    return NavigationState(**defaults)


class TestNextMovie:
    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_delegates_to_navigator(self):
        mm = MovieManager(db_config=None)
        mm._navigator = AsyncMock()
        mm._navigator.next_movie = AsyncMock(return_value="redirect-response")

        state = _state()
        result = await mm.next_movie(state)

        assert result == "redirect-response"
        mm._navigator.next_movie.assert_awaited_once_with(
            "state-1", legacy_session=None
        )

    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_returns_none_when_navigator_missing(self):
        mm = MovieManager(db_config=None)
        mm._navigator = None

        result = await mm.next_movie(_state())
        assert result is None

    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_returns_none_when_state_is_none(self):
        mm = MovieManager(db_config=None)
        mm._navigator = AsyncMock()

        result = await mm.next_movie(None)
        assert result is None
        mm._navigator.next_movie.assert_not_awaited()


class TestPreviousMovie:
    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_delegates_to_navigator(self):
        mm = MovieManager(db_config=None)
        mm._navigator = AsyncMock()
        mm._navigator.previous_movie = AsyncMock(return_value="prev-redirect")

        state = _state()
        result = await mm.previous_movie(state)

        assert result == "prev-redirect"
        mm._navigator.previous_movie.assert_awaited_once_with(
            "state-1", legacy_session=None
        )

    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_returns_none_when_navigator_missing(self):
        mm = MovieManager(db_config=None)
        mm._navigator = None

        result = await mm.previous_movie(_state())
        assert result is None


class TestRenderMovieByTconst:
    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_delegates_with_previous_count(self):
        mm = MovieManager(db_config=None)
        mm._navigator = AsyncMock()
        mm._navigator.prev_stack_length = lambda state: 3
        mm._renderer = AsyncMock()
        mm._renderer.render_movie_by_tconst = AsyncMock(return_value="<html>movie</html>")

        state = _state()
        result = await mm.render_movie_by_tconst(state, "tt1234567")

        assert result == "<html>movie</html>"
        mm._renderer.render_movie_by_tconst.assert_awaited_once_with(
            "tt1234567",
            previous_count=3,
            template_name="movie.html",
        )

    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_previous_count_zero_when_no_navigator(self):
        mm = MovieManager(db_config=None)
        mm._navigator = None
        mm._renderer = AsyncMock()
        mm._renderer.render_movie_by_tconst = AsyncMock(return_value="<html>")

        result = await mm.render_movie_by_tconst(None, "tt1234567")

        mm._renderer.render_movie_by_tconst.assert_awaited_once_with(
            "tt1234567",
            previous_count=0,
            template_name="movie.html",
        )


class TestGetCurrentMovieTconst:
    @patch.dict(os.environ, TEST_ENV)
    def test_returns_current_tconst(self):
        mm = MovieManager(db_config=None)
        mm._navigator = AsyncMock()
        mm._navigator.get_current_movie_tconst = lambda state: state.current_tconst

        state = _state(current_tconst="tt999")
        assert mm.get_current_movie_tconst(state) == "tt999"

    @patch.dict(os.environ, TEST_ENV)
    def test_returns_none_when_no_state(self):
        mm = MovieManager(db_config=None)
        mm._navigator = AsyncMock()
        assert mm.get_current_movie_tconst(None) is None


class TestLogout:
    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_delegates_delete_state(self):
        mm = MovieManager(db_config=None)
        mm.navigation_state_store = AsyncMock()
        mm.navigation_state_store.delete_state = AsyncMock()

        state = _state()
        await mm.logout(state)

        mm.navigation_state_store.delete_state.assert_awaited_once_with(
            "state-1", legacy_session=None
        )

    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_noop_when_state_is_none(self):
        mm = MovieManager(db_config=None)
        mm.navigation_state_store = AsyncMock()

        await mm.logout(None)
        mm.navigation_state_store.delete_state.assert_not_awaited()


class TestClose:
    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_closes_tmdb_and_pool(self):
        mm = MovieManager(db_config=None)
        mm.tmdb_helper = AsyncMock()
        mm.tmdb_helper.close = AsyncMock()
        mm.db_pool = AsyncMock()
        mm.db_pool.close_pool = AsyncMock()

        await mm.close()

        mm.tmdb_helper.close.assert_awaited_once()
        mm.db_pool.close_pool.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.dict(os.environ, TEST_ENV)
    async def test_close_continues_on_tmdb_error(self):
        mm = MovieManager(db_config=None)
        mm.tmdb_helper = AsyncMock()
        mm.tmdb_helper.close = AsyncMock(side_effect=RuntimeError("close fail"))
        mm.db_pool = AsyncMock()
        mm.db_pool.close_pool = AsyncMock()

        await mm.close()
        # db_pool.close_pool should still be called
        mm.db_pool.close_pool.assert_awaited_once()
