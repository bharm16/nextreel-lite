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
            "public_id": "a8fk3j",
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
        result = data["results"][0]
        assert result["title"] == "Chungking Express"
        assert result["year"] == 1994
        assert result["rating"] == 8.1
        # URL is canonical /movie/<slug>-<public_id>; tconst is no longer
        # exposed (clients should rely on the opaque public_id only).
        assert result["url"] == "/movie/chungking-express-1994-a8fk3j"
        assert "tconst" not in result


@pytest.mark.asyncio
async def test_search_route_skips_rows_without_public_id(test_client):
    """Candidates with no projection row yet (NULL public_id) aren't navigable.

    The route omits them from results rather than returning broken URLs that
    the new public_id router would 404 on.
    """
    fake_rows = [
        {
            "tconst": "tt0000001",
            "public_id": None,
            "primaryTitle": "Unenriched",
            "startYear": 2024,
            "averageRating": 5.0,
        },
        {
            "tconst": "tt0109424",
            "public_id": "a8fk3j",
            "primaryTitle": "Chungking Express",
            "startYear": 1994,
            "averageRating": 8.1,
        },
    ]
    with patch(
        "nextreel.web.routes.search._execute_search",
        new=AsyncMock(return_value=fake_rows),
    ):
        response = await test_client.get("/api/search?q=test")
        assert response.status_code == 200
        data = await response.get_json()
        assert len(data["results"]) == 1
        assert data["results"][0]["title"] == "Chungking Express"


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


@pytest.mark.asyncio
async def test_projection_state_resolves_public_id(test_client):
    """The route accepts a 6-char public_id and returns the projection state."""
    with patch(
        "nextreel.web.routes.shared.resolve_to_tconst",
        new=AsyncMock(return_value="tt0393109"),
    ):
        with patch("nextreel.web.routes.search._services") as services:
            services.return_value.movie_manager.projection_store.select_row = AsyncMock(
                return_value={"projection_state": "ready"}
            )
            response = await test_client.get("/api/projection-state/a8fk3j")
            assert response.status_code == 200
            body = await response.get_json()
            assert body == {"state": "ready"}


@pytest.mark.asyncio
async def test_projection_state_404_for_imdb_tconst(test_client):
    """Old IMDb-shaped paths return 404, not 400."""
    response = await test_client.get("/api/projection-state/tt0393109")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_projection_state_404_for_unknown_id(test_client):
    with patch(
        "nextreel.web.routes.shared.resolve_to_tconst",
        new=AsyncMock(return_value=None),
    ):
        response = await test_client.get("/api/projection-state/zzzzzz")
        assert response.status_code == 404
