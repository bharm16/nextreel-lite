"""Tests for the default_filters injection into template context."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

from quart import g, render_template_string

from tests.helpers import TEST_ENV


def _make_app():
    """Create a test app with a mocked MovieManager."""
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.next_movie = AsyncMock(return_value=None)
        manager.previous_movie = AsyncMock(return_value=None)
        manager.apply_filters = AsyncMock(return_value=None)
        manager.count_matching_movies = AsyncMock(return_value=42)

        # Mock db_pool with execute method for queries
        mock_db_pool = MagicMock()
        mock_db_pool.execute = AsyncMock(return_value=None)
        manager.db_pool = mock_db_pool

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


# The JSON island lives only on movie.html (the only page that loads
# filter-drawer.js). Rather than fully mocking the movie detail render path,
# we render the island markup directly under a request context so the
# context processor result hits a real Jinja render — which is exactly what
# the production template does at templates/movie.html:108.
_JSON_ISLAND_TEMPLATE = (
    '<script type="application/json" id="default-filters-data">'
    "{{ default_filters | tojson }}</script>"
)


async def test_default_filters_present_for_anonymous_user():
    """Anonymous users get the system "Any" baselines via context processor.

    This exercises the inject_default_filters context processor through a real
    Jinja render — the same path templates/movie.html:108 takes — so any
    breakage in the processor surface (g.navigation_state lookup, fallback
    when no user_id, |tojson serialization) shows up as a test failure.
    """
    app, _manager = _make_app()
    async with app.app_context():
        async with app.test_request_context("/"):
            # Anonymous: no navigation_state.user_id present.
            rendered = await render_template_string(_JSON_ISLAND_TEMPLATE)

    # Extract the JSON payload between the script tags.
    payload_text = rendered.split(">", 1)[1].rsplit("<", 1)[0]
    payload = json.loads(payload_text)

    # System "Any" baselines per plan 2026-05-03 Task 1.
    assert payload["imdb_score_min"] == 1.0
    assert payload["imdb_score_max"] == 10.0
    assert payload["num_votes_min"] == 0
    assert payload["num_votes_max"] == 2000000
    assert payload["language"] == "any"
    assert payload["exclude_watched"] is True
    assert payload["exclude_watchlist"] is True


async def test_default_filters_are_user_saved_flag_false_for_anonymous():
    """Anonymous users get default_filters_are_user_saved=False so the
    drawer's reset link reads "Reset to defaults" rather than "Reset to my
    defaults"."""
    app, _manager = _make_app()
    async with app.app_context():
        async with app.test_request_context("/"):
            rendered = await render_template_string("{{ default_filters_are_user_saved }}")
    assert rendered == "False"
