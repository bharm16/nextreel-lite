"""Tests for the MySQL-backed MovieNavigator."""

import inspect

import pytest
from quart import Quart

from infra.filter_normalizer import default_filter_state
from nextreel.domain.navigation_state import MutationResult, NavigationState
from infra.time_utils import utcnow
from nextreel.application.movie_navigator import MovieNavigator, NavigationOutcome, _movie_ref
from nextreel.web.routes import bp


def _state(*, user_id: str | None = None, exclude_watched: bool = True) -> NavigationState:
    now = utcnow()
    return NavigationState(
        session_id="state-1",
        version=1,
        csrf_token="csrf",
        filters={**default_filter_state(), "exclude_watched": exclude_watched},
        current_tconst=None,
        current_ref=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
        user_id=user_id,
    )


class CandidateStoreStub:
    def __init__(self, refs=None, ref_map=None):
        self.refs = refs or []
        self.ref_map = ref_map or {}
        self.fetch_candidate_refs_calls = []
        self.fetch_ref_calls = []

    async def fetch_candidate_refs(self, filters, excluded_tconsts, limit):
        self.fetch_candidate_refs_calls.append((filters, set(excluded_tconsts), limit))
        return list(self.refs[:limit])

    async def fetch_ref(self, tconst):
        self.fetch_ref_calls.append(tconst)
        return self.ref_map.get(tconst)


class NavigationStoreStub:
    def __init__(self, state):
        self.state = state
        self.should_conflict = False

    async def mutate(self, session_id, mutator, legacy_session=None, current_state=None):
        if self.should_conflict:
            return MutationResult(state=self.state, conflicted=True)
        working = current_state.clone() if current_state is not None else self.state.clone()
        result = mutator(working)
        if inspect.isawaitable(result):
            result = await result
        self.state = working
        return MutationResult(state=self.state, result=result, conflicted=False)


class WatchedStoreStub:
    def __init__(self, watched):
        self.watched = set(watched)
        self.calls = []

    async def watched_tconsts(self, user_id):
        self.calls.append(user_id)
        return set(self.watched)


@pytest.fixture
def nav_app():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(bp)
    return app


def test_movie_ref_extracts_new_contract():
    ref = _movie_ref({"imdb_id": "tt123", "title": "Test", "slug": "test"})
    # public_id and year were added so navigator stack entries can carry
    # the URL identifier and year without an extra DB lookup. Both fall
    # back to None when the source dict lacks them (pre-enrichment /
    # legacy callers).
    assert ref == {
        "tconst": "tt123",
        "title": "Test",
        "slug": "test",
        "public_id": None,
        "year": None,
    }


@pytest.mark.asyncio
async def test_prewarm_queue_populates_empty_queue(nav_app):
    state = _state()
    refs = [{"tconst": "tt1", "title": "One", "slug": "one"}]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub(refs=refs)
    navigator = MovieNavigator(candidates, store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            updated_state = await navigator.prewarm_queue("state-1")

    assert updated_state.queue == refs
    assert candidates.fetch_candidate_refs_calls


@pytest.mark.asyncio
async def test_next_movie_consumes_queue_and_tracks_history(nav_app):
    state = _state()
    state.queue = [
        {"tconst": "tt1", "title": "One", "slug": "one"},
        {"tconst": "tt2", "title": "Two", "slug": "two"},
    ]
    state.current_tconst = "tt0"
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub(
        ref_map={"tt0": {"tconst": "tt0", "title": "Zero", "slug": "zero"}},
    )
    navigator = MovieNavigator(candidates, store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1")

    assert outcome is not None and outcome.tconst == "tt1"
    assert outcome.title == "One"
    assert store.state.current_tconst == "tt1"
    assert store.state.prev == [{"tconst": "tt0", "title": "Zero", "slug": "zero"}]
    assert "tt1" in store.state.seen


@pytest.mark.asyncio
async def test_previous_movie_moves_current_into_future(nav_app):
    state = _state()
    state.current_tconst = "tt2"
    state.prev = [{"tconst": "tt1", "title": "One", "slug": "one"}]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub(
        ref_map={"tt2": {"tconst": "tt2", "title": "Two", "slug": "two"}},
    )
    navigator = MovieNavigator(candidates, store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.previous_movie("state-1")

    assert outcome is not None and outcome.tconst == "tt1"
    assert outcome.title == "One"
    assert store.state.current_tconst == "tt1"
    assert store.state.future == [{"tconst": "tt2", "title": "Two", "slug": "two"}]


@pytest.mark.asyncio
async def test_next_movie_uses_current_ref_without_fetch_lookup(nav_app):
    state = _state()
    state.current_tconst = "tt0"
    state.current_ref = {"tconst": "tt0", "title": "Zero", "slug": "zero"}
    state.queue = [{"tconst": "tt1", "title": "One", "slug": "one"}]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub()
    navigator = MovieNavigator(candidates, store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1", current_state=state)

    assert outcome is not None and outcome.tconst == "tt1"
    assert outcome.title == "One"
    # _movie_ref normalizes refs and includes public_id and year (None
    # for legacy state that predates these fields on the ref).
    assert store.state.prev == [
        {
            "tconst": "tt0",
            "title": "Zero",
            "slug": "zero",
            "public_id": None,
            "year": None,
        }
    ]
    assert candidates.fetch_ref_calls == []


@pytest.mark.asyncio
async def test_next_movie_only_refills_once_when_queue_starts_empty(nav_app):
    state = _state()
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub(refs=[{"tconst": "tt1", "title": "One", "slug": "one"}])
    navigator = MovieNavigator(candidates, store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1")

    assert outcome is not None and outcome.tconst == "tt1"
    assert outcome.title == "One"
    assert len(candidates.fetch_candidate_refs_calls) == 1


@pytest.mark.asyncio
async def test_next_movie_skips_watched_refs_already_in_queue(nav_app):
    state = _state(user_id="user-1", exclude_watched=True)
    state.queue = [
        {"tconst": "tt1", "title": "One", "slug": "one"},
        {"tconst": "tt2", "title": "Two", "slug": "two"},
    ]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub()
    watched_store = WatchedStoreStub({"tt1"})
    navigator = MovieNavigator(candidates, store, watched_store=watched_store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1")

    assert outcome is not None and outcome.tconst == "tt2"
    assert outcome.title == "Two"
    assert store.state.current_tconst == "tt2"
    assert store.state.queue == []
    assert watched_store.calls == ["user-1"]


@pytest.mark.asyncio
async def test_next_movie_refills_when_all_prefetched_refs_are_now_watched(nav_app):
    state = _state(user_id="user-1", exclude_watched=True)
    state.queue = [{"tconst": "tt1", "title": "One", "slug": "one"}]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub(
        refs=[{"tconst": "tt2", "title": "Two", "slug": "two"}],
    )
    watched_store = WatchedStoreStub({"tt1"})
    navigator = MovieNavigator(candidates, store, watched_store=watched_store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1")

    assert outcome is not None and outcome.tconst == "tt2"
    assert outcome.title == "Two"
    assert store.state.current_tconst == "tt2"
    assert watched_store.calls
    assert candidates.fetch_candidate_refs_calls


@pytest.mark.asyncio
async def test_next_movie_does_not_skip_queued_watched_when_exclude_watched_off(nav_app):
    state = _state(user_id="user-1", exclude_watched=False)
    state.queue = [{"tconst": "tt1", "title": "One", "slug": "one"}]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub()
    watched_store = WatchedStoreStub({"tt1"})
    navigator = MovieNavigator(candidates, store, watched_store=watched_store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1")

    assert outcome is not None and outcome.tconst == "tt1"
    assert outcome.title == "One"
    assert store.state.current_tconst == "tt1"
    assert watched_store.calls == []


@pytest.mark.asyncio
async def test_apply_filters_resets_state_and_redirects(nav_app):
    state = _state()
    state.current_tconst = "tt9"
    state.prev = [{"tconst": "tt8", "title": "Old", "slug": "old"}]
    state.queue = [{"tconst": "tt7", "title": "Queue", "slug": "queue"}]
    store = NavigationStoreStub(state)
    candidates = CandidateStoreStub(refs=[{"tconst": "tt5", "title": "Fresh", "slug": "fresh"}])
    navigator = MovieNavigator(candidates, store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.apply_filters(
                "state-1",
                {"language": "fr", "genres_selected": ["Drama"]},
            )

    assert outcome is not None and outcome.tconst == "tt5"
    assert outcome.title == "Fresh"
    assert store.state.current_tconst == "tt5"
    assert store.state.prev == []
    assert store.state.future == []
    assert store.state.seen == ["tt5"]
    assert store.state.filters["language"] == "fr"


@pytest.mark.asyncio
async def test_conflict_redirects_to_current_movie(nav_app):
    state = _state()
    state.current_tconst = "tt2"
    store = NavigationStoreStub(state)
    store.should_conflict = True
    navigator = MovieNavigator(CandidateStoreStub(), store)

    async with nav_app.app_context():
        async with nav_app.test_request_context("/"):
            outcome = await navigator.next_movie("state-1")

    assert outcome == NavigationOutcome(tconst="tt2", state_conflict=True)
