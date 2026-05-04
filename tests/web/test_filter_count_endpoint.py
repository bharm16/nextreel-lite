"""Tests for the POST /api/filter_count endpoint."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import TEST_ENV


def _make_app(count_return_value: int = 42):
    """Create a test app with a mocked MovieManager that returns a fixed count."""
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.next_movie = AsyncMock(return_value=None)
        manager.previous_movie = AsyncMock(return_value=None)
        manager.apply_filters = AsyncMock(return_value=None)
        manager.count_matching_movies = AsyncMock(return_value=count_return_value)
        manager.db_pool = object()
        manager.get_current_movie_tconst = MagicMock(return_value=None)
        manager.logout = AsyncMock()
        manager.projection_store = MagicMock()
        manager.projection_store.coordinator = MagicMock()
        manager.projection_store.coordinator._inflight_enrichment = {}
        manager.projection_store.coordinator.has_inflight = MagicMock(return_value=False)
        manager.watched_store = MagicMock()
        manager.watched_store.is_watched = AsyncMock(return_value=False)
        navigator = MagicMock()
        navigator.prev_stack_length = MagicMock(return_value=0)
        manager._navigator = navigator

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        return app, manager


_VALID_FILTER_FORM = {
    "year_min": "2010",
    "year_max": "2020",
    "imdb_score_min": "7.0",
    "imdb_score_max": "10.0",
    "num_votes_min": "0",
    "num_votes_max": "2000000",
    "language": "any",
    "exclude_watched": "off",
    "exclude_watchlist": "off",
}


async def test_filter_count_returns_int():
    """Endpoint returns an integer count for a valid filter payload."""
    app, _manager = _make_app(count_return_value=37)
    async with app.app_context():
        client = app.test_client()
        response = await client.post(
            "/api/filter_count",
            headers={"X-CSRFToken": "test-csrf-token"},
            form=_VALID_FILTER_FORM,
        )
    assert response.status_code == 200
    payload = await response.get_json()
    assert "count" in payload
    assert isinstance(payload["count"], int)
    assert payload["count"] == 37


async def test_filter_count_rejects_invalid_filters():
    """Invalid filter values return 400 with errors, no count."""
    app, _manager = _make_app()
    async with app.app_context():
        client = app.test_client()
        response = await client.post(
            "/api/filter_count",
            headers={"X-CSRFToken": "test-csrf-token"},
            form={
                "year_min": "9999",
                "year_max": "2020",
                "imdb_score_min": "7.0",
                "imdb_score_max": "10.0",
                "num_votes_min": "0",
                "num_votes_max": "2000000",
                "language": "any",
            },
        )
    assert response.status_code == 400
    payload = await response.get_json()
    assert payload["ok"] is False
    assert "errors" in payload


async def test_filter_count_requires_csrf():
    """Endpoint enforces CSRF like other state-shaped POSTs."""
    app, _manager = _make_app()
    async with app.app_context():
        client = app.test_client()
        response = await client.post(
            "/api/filter_count",
            form=_VALID_FILTER_FORM,
        )
    assert response.status_code in (400, 403)


async def test_filter_count_passes_normalized_filterstate_to_manager():
    """Form payload reaches count_matching_movies as a normalized FilterState.

    Without this assertion, a regression where ``normalize_filters`` mis-parsed
    the payload (e.g. dropped ``language``, miscoerced numeric fields, ignored
    chip-driven hidden inputs) would still produce a 200 response with whatever
    count the mock returned. This test pins down that the form values actually
    bind to the FilterState fields the manager receives.
    """
    app, manager = _make_app(count_return_value=5)
    async with app.app_context():
        client = app.test_client()
        response = await client.post(
            "/api/filter_count",
            headers={"X-CSRFToken": "test-csrf-token"},
            form={
                "year_min": "1995",
                "year_max": "2005",
                "imdb_score_min": "6.5",
                "imdb_score_max": "9.5",
                "num_votes_min": "500",
                "num_votes_max": "100000",
                "language": "fr",
                "exclude_watched": "off",
                "exclude_watchlist": "off",
            },
        )
    assert response.status_code == 200

    # The route calls manager.count_matching_movies(state, filters, legacy_session=...)
    # — pull the FilterState out of call_args and verify each field bound correctly.
    # ``normalize_filters`` keeps the numeric fields as strings; coercion to
    # int/float happens downstream in ``criteria_from_filters`` /
    # ``validate_filters``. Assert against the post-normalize shape.
    assert manager.count_matching_movies.await_count == 1
    args, kwargs = manager.count_matching_movies.call_args
    # state is positional [0]; filters is positional [1]
    filters = args[1]
    assert filters["year_min"] == "1995"
    assert filters["year_max"] == "2005"
    assert filters["imdb_score_min"] == "6.5"
    assert filters["imdb_score_max"] == "9.5"
    assert filters["num_votes_min"] == "500"
    assert filters["num_votes_max"] == "100000"
    assert filters["language"] == "fr"
    # "off" values must not flip these to True — exclude flags follow the
    # checkbox-with-hidden-input pattern from templates/_filter_form.html.
    assert filters["exclude_watched"] is False
    assert filters["exclude_watchlist"] is False
