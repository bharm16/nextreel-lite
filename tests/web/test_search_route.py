"""Tests for the /api/search JSON endpoint."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import TEST_ENV


@pytest.fixture
def test_client():
    """Create a Quart test client with a mocked MovieManager.

    Mirrors the inline pattern used in tests/web/test_app.py — we build the
    real app (so the /api/search route registers via blueprint), but stub out
    MovieManager so no DB/Redis I/O happens. The search route resolves its
    pool through ``_services().movie_manager.db_pool``; tests patch
    ``_execute_search`` directly to bypass that path.
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


@pytest.mark.asyncio
async def test_search_route_returns_empty_for_missing_query(test_client):
    response = await test_client.get("/api/search")
    assert response.status_code == 200
    data = await response.get_json()
    assert data == {"results": []}


@pytest.mark.asyncio
async def test_search_route_returns_empty_for_short_query(test_client):
    response = await test_client.get("/api/search?q=a")
    assert response.status_code == 200
    data = await response.get_json()
    assert data == {"results": []}


@pytest.mark.asyncio
async def test_search_route_returns_results_for_valid_query(test_client):
    fake_rows = [
        {
            "tconst": "tt0109424",
            "primaryTitle": "Chungking Express",
            "startYear": 1994,
            "averageRating": 8.1,
        }
    ]
    with patch(
        "nextreel.web.routes.search._execute_search",
        new=AsyncMock(return_value=fake_rows),
    ):
        response = await test_client.get("/api/search?q=chungking")
        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["results"]) == 1
        assert data["results"][0]["tconst"] == "tt0109424"
        assert data["results"][0]["title"] == "Chungking Express"
        assert data["results"][0]["year"] == 1994
        assert data["results"][0]["rating"] == 8.1


@pytest.mark.asyncio
async def test_search_route_handles_database_error_gracefully(test_client):
    with patch(
        "nextreel.web.routes.search._execute_search",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        response = await test_client.get("/api/search?q=drama")
        assert response.status_code == 200  # degrade gracefully — never 500
        data = await response.get_json()
        assert data == {"results": []}
