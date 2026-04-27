"""Tests for the /movie/<slug_with_id> route."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import TEST_ENV


@pytest.fixture
def test_client():
    """Create a Quart test client with a mocked MovieManager.

    Mirrors the inline pattern used in tests/web/test_search_route.py — we
    build the real app (so the /movie/<slug_with_id> route registers via
    blueprint), but stub out MovieManager so no DB/Redis I/O happens.
    Tests patch ``_movie_detail_service`` and ``resolve_to_tconst`` to drive
    the handler from a known view-model payload.
    """
    with patch.dict(os.environ, TEST_ENV), patch("app.MovieManager") as MockManager:
        manager = MockManager.return_value
        manager.home = AsyncMock(return_value={"default_backdrop_url": None})
        manager.db_pool = MagicMock()
        manager.db_pool.execute = AsyncMock(return_value=[])

        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        yield app.test_client()


def _view_model(payload: dict, *, previous_count: int = 0,
                is_watched: bool = False, is_in_watchlist: bool = False):
    return type(
        "VM",
        (),
        {
            "movie": payload,
            "previous_count": previous_count,
            "is_watched": is_watched,
            "is_in_watchlist": is_in_watchlist,
        },
    )()


@pytest.mark.asyncio
async def test_movie_detail_renders_when_slug_canonical(test_client):
    payload = {
        "tconst": "tt0393109",
        "title": "The Departed",
        "primaryTitle": "The Departed",
        "year": "2006",
        "public_id": "a8fk3j",
        "_full": True,
    }
    with patch(
        "nextreel.web.routes.shared.resolve_to_tconst",
        new=AsyncMock(return_value="tt0393109"),
    ):
        with patch("nextreel.web.routes.movies._movie_detail_service") as svc:
            svc.get = AsyncMock(return_value=_view_model(payload))
            with patch(
                "nextreel.web.routes.movies.render_template",
                new=AsyncMock(return_value="<html>movie</html>"),
            ):
                response = await test_client.get("/movie/the-departed-2006-a8fk3j")
                assert response.status_code == 200


@pytest.mark.asyncio
async def test_movie_detail_redirects_to_canonical_on_slug_mismatch(test_client):
    payload = {
        "tconst": "tt0393109",
        "primaryTitle": "The Departed",
        "year": "2006",
        "public_id": "a8fk3j",
        "_full": True,
    }
    with patch(
        "nextreel.web.routes.shared.resolve_to_tconst",
        new=AsyncMock(return_value="tt0393109"),
    ):
        with patch("nextreel.web.routes.movies._movie_detail_service") as svc:
            svc.get = AsyncMock(return_value=_view_model(payload))
            response = await test_client.get("/movie/wrong-slug-a8fk3j")
            assert response.status_code == 301
            assert response.headers["Location"].endswith("/movie/the-departed-2006-a8fk3j")


@pytest.mark.asyncio
async def test_movie_detail_404_for_imdb_tconst_url(test_client):
    """Old /movie/tt0393109 URLs return 404 (hard break, no redirect)."""
    response = await test_client.get("/movie/tt0393109")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_movie_detail_404_for_unknown_id(test_client):
    with patch(
        "nextreel.web.routes.shared.resolve_to_tconst",
        new=AsyncMock(return_value=None),
    ):
        response = await test_client.get("/movie/anything-aaaaaa")
        assert response.status_code == 404
