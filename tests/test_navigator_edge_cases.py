"""Edge-case tests for MovieNavigator — empty states, stack bounds, refill failures.

Targets:
  1. next_movie with empty queue AND empty candidates (refill returns nothing)
  2. previous_movie with empty prev stack
  3. prev stack overflow at PREV_STACK_MAX
  4. future stack overflow at FUTURE_STACK_MAX
  5. seen list overflow at SEEN_MAX
  6. conflict redirect when state.current_tconst is None
  7. apply_filters when candidates are empty
"""

import inspect

import pytest
from quart import Quart

from infra.navigation_state import (
    FUTURE_STACK_MAX,
    PREV_STACK_MAX,
    QUEUE_REFILL_THRESHOLD,
    QUEUE_TARGET,
    SEEN_MAX,
    MutationResult,
    NavigationState,
    default_filter_state,
    utcnow,
)
from movie_navigator import MovieNavigator, _movie_ref
from routes import bp


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


class CandidateStoreStub:
    def __init__(self, refs=None, ref_map=None):
        self.refs = refs or []
        self.ref_map = ref_map or {}

    async def fetch_candidate_refs(self, filters, excluded_tconsts, limit):
        return list(self.refs[:limit])

    async def fetch_ref(self, tconst):
        return self.ref_map.get(tconst)


class NavigationStoreStub:
    def __init__(self, state, should_conflict=False):
        self.state = state
        self.should_conflict = should_conflict

    async def mutate(self, session_id, mutator, legacy_session=None):
        if self.should_conflict:
            return MutationResult(state=self.state, conflicted=True)
        working = self.state.clone()
        result = mutator(working)
        if inspect.isawaitable(result):
            result = await result
        self.state = working
        return MutationResult(state=self.state, result=result, conflicted=False)


@pytest.fixture
def nav_app():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(bp)
    return app


# ═══════════════════════════════════════════════════════════════════════
# 1. next_movie — queue exhaustion
# ═══════════════════════════════════════════════════════════════════════


class TestNextMovieEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_queue_no_candidates_returns_none(self, nav_app):
        """When queue is empty and refill returns nothing, response is None."""
        state = _state()
        store = NavigationStoreStub(state)
        candidates = CandidateStoreStub()  # empty

        navigator = MovieNavigator(candidates, store)
        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                response, updated_state = await navigator.next_movie("state-1")

        assert response is None

    @pytest.mark.asyncio
    async def test_future_stack_consumed_before_queue(self, nav_app):
        """Future stack should be consumed first (LIFO)."""
        state = _state(
            future=[
                {"tconst": "tt_future", "title": "Future", "slug": "future"},
            ],
            queue=[
                {"tconst": "tt_queue", "title": "Queue", "slug": "queue"},
            ],
        )
        store = NavigationStoreStub(state)
        navigator = MovieNavigator(CandidateStoreStub(), store)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                response, _ = await navigator.next_movie("state-1")

        assert response.location.endswith("/movie/tt_future")
        assert store.state.current_tconst == "tt_future"
        # Queue should be untouched
        assert len(store.state.queue) == 1

    @pytest.mark.asyncio
    async def test_prev_stack_bounded_at_max(self, nav_app):
        """Prev stack should not exceed PREV_STACK_MAX."""
        prev = [{"tconst": f"tt{i}", "title": f"P{i}", "slug": f"p{i}"} for i in range(PREV_STACK_MAX)]
        state = _state(
            current_tconst="tt_current",
            prev=prev,
            queue=[{"tconst": "tt_next", "title": "Next", "slug": "next"}],
        )
        store = NavigationStoreStub(state)
        candidates = CandidateStoreStub(
            ref_map={"tt_current": {"tconst": "tt_current", "title": "Current", "slug": "current"}},
        )
        navigator = MovieNavigator(candidates, store)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                await navigator.next_movie("state-1")

        assert len(store.state.prev) == PREV_STACK_MAX
        # Oldest entry should have been evicted
        assert store.state.prev[-1]["tconst"] == "tt_current"


# ═══════════════════════════════════════════════════════════════════════
# 2. previous_movie — empty stack
# ═══════════════════════════════════════════════════════════════════════


class TestPreviousMovieEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_prev_returns_none(self, nav_app):
        """No previous movies → response is None."""
        state = _state(current_tconst="tt1")
        store = NavigationStoreStub(state)
        navigator = MovieNavigator(CandidateStoreStub(), store)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                response, _ = await navigator.previous_movie("state-1")

        assert response is None

    @pytest.mark.asyncio
    async def test_future_stack_bounded_at_max(self, nav_app):
        """Future stack should not exceed FUTURE_STACK_MAX."""
        future = [{"tconst": f"tt{i}", "title": f"F{i}", "slug": f"f{i}"} for i in range(FUTURE_STACK_MAX)]
        state = _state(
            current_tconst="tt_current",
            prev=[{"tconst": "tt_prev", "title": "Prev", "slug": "prev"}],
            future=future,
        )
        store = NavigationStoreStub(state)
        candidates = CandidateStoreStub(
            ref_map={"tt_current": {"tconst": "tt_current", "title": "Current", "slug": "current"}},
        )
        navigator = MovieNavigator(candidates, store)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                await navigator.previous_movie("state-1")

        assert len(store.state.future) <= FUTURE_STACK_MAX


# ═══════════════════════════════════════════════════════════════════════
# 3. Seen list overflow
# ═══════════════════════════════════════════════════════════════════════


class TestSeenOverflow:
    @pytest.mark.asyncio
    async def test_seen_bounded_at_max(self, nav_app):
        """Seen list should not exceed SEEN_MAX."""
        state = _state(
            seen=[f"tt{i}" for i in range(SEEN_MAX)],
            queue=[{"tconst": "tt_new", "title": "New", "slug": "new"}],
        )
        store = NavigationStoreStub(state)
        navigator = MovieNavigator(CandidateStoreStub(), store)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                await navigator.next_movie("state-1")

        assert len(store.state.seen) <= SEEN_MAX
        assert "tt_new" in store.state.seen  # newest entry preserved

    @pytest.mark.asyncio
    async def test_seen_deduplication(self, nav_app):
        """Revisiting a movie should not add duplicate seen entries."""
        state = _state(
            seen=["tt1"],
            future=[{"tconst": "tt1", "title": "Repeat", "slug": "repeat"}],
        )
        store = NavigationStoreStub(state)
        navigator = MovieNavigator(CandidateStoreStub(), store)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                await navigator.next_movie("state-1")

        assert store.state.seen.count("tt1") == 1


# ═══════════════════════════════════════════════════════════════════════
# 4. Conflict redirect edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestConflictRedirect:
    @pytest.mark.asyncio
    async def test_conflict_with_no_current_tconst_redirects_home(self, nav_app):
        """BUG FINDER: If conflict and state.current_tconst is None,
        should redirect to home, not crash.
        """
        state = _state(current_tconst=None)
        store = NavigationStoreStub(state, should_conflict=True)
        navigator = MovieNavigator(CandidateStoreStub(), store)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                response, _ = await navigator.next_movie("state-1")

        assert response.status_code == 303
        # When current_tconst is None, _conflict_redirect redirects to home
        assert "state_conflict=1" in response.location

    @pytest.mark.asyncio
    async def test_conflict_with_none_state_redirects_home(self, nav_app):
        """BUG FINDER: If conflict and state is None (from expired session).

        _conflict_redirect handles state=None with the 'if state and ...' guard.
        """
        navigator = MovieNavigator(CandidateStoreStub(), None)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                result = navigator._conflict_redirect(None)

        assert result.status_code == 303


# ═══════════════════════════════════════════════════════════════════════
# 5. apply_filters — empty candidates
# ═══════════════════════════════════════════════════════════════════════


class TestApplyFiltersEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_candidates_returns_none(self, nav_app):
        """When no movies match the filter, response is None."""
        state = _state(
            current_tconst="tt1",
            queue=[{"tconst": "tt2", "title": "Q", "slug": "q"}],
        )
        store = NavigationStoreStub(state)
        navigator = MovieNavigator(CandidateStoreStub(), store)

        async with nav_app.app_context():
            async with nav_app.test_request_context("/"):
                response, _ = await navigator.apply_filters("state-1", {"language": "xx"})

        assert response is None
        # State should have been cleared even though no results
        assert store.state.queue == []
        assert store.state.prev == []
        assert store.state.current_tconst is None


# ═══════════════════════════════════════════════════════════════════════
# 6. _movie_ref edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestMovieRef:
    def test_prefers_tconst_over_imdb_id(self):
        ref = _movie_ref({"tconst": "tt1", "imdb_id": "tt2", "title": "T", "slug": "s"})
        assert ref["tconst"] == "tt1"

    def test_falls_back_to_imdb_id(self):
        ref = _movie_ref({"imdb_id": "tt2", "title": "T", "slug": "s"})
        assert ref["tconst"] == "tt2"

    def test_missing_both_returns_none_tconst(self):
        ref = _movie_ref({"title": "T"})
        assert ref["tconst"] is None
