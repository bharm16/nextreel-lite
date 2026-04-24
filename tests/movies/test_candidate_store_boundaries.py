from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def test_candidate_filter_pool_cache_lives_outside_candidate_store():
    from movies.candidate_filter_pool_cache import CandidateFilterPoolCache

    assert CandidateFilterPoolCache.__module__ == "movies.candidate_filter_pool_cache"


def test_candidate_store_owns_filter_pool_cache_collaborator():
    from movies.candidate_filter_pool_cache import CandidateFilterPoolCache
    from movies.candidate_store import CandidateStore

    store = CandidateStore(AsyncMock())

    assert isinstance(store._filter_pool_cache, CandidateFilterPoolCache)


@pytest.mark.asyncio
async def test_candidate_store_delegates_filter_pool_cache_lookup():
    from movies.candidate_store import CandidateStore

    store = CandidateStore(AsyncMock())
    store._filter_pool_cache.sample = AsyncMock(return_value=[{"tconst": "tt1"}])

    refs = await store.fetch_candidate_refs_for_criteria(
        {"language": "any"},
        excluded_tconsts=set(),
        limit=1,
    )

    assert refs == [{"tconst": "tt1"}]
    store._filter_pool_cache.sample.assert_awaited_once()
