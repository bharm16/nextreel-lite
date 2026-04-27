"""Tests for the public_id → tconst route helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from werkzeug.exceptions import HTTPException

from nextreel.web.routes.shared import _resolve_public_id_or_404


async def test_returns_tconst_for_known_id(app):
    async with app.test_request_context("/"):
        with patch("nextreel.web.routes.shared._services") as services:
            services.return_value.movie_manager.db_pool = AsyncMock()
            with patch(
                "nextreel.web.routes.shared.resolve_to_tconst",
                new=AsyncMock(return_value="tt0393109"),
            ):
                result = await _resolve_public_id_or_404("a8fk3j")
                assert result == "tt0393109"


async def test_aborts_404_for_unknown(app):
    async with app.test_request_context("/"):
        with patch("nextreel.web.routes.shared._services") as services:
            services.return_value.movie_manager.db_pool = AsyncMock()
            with patch(
                "nextreel.web.routes.shared.resolve_to_tconst",
                new=AsyncMock(return_value=None),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await _resolve_public_id_or_404("a8fk3j")
                assert exc_info.value.code == 404


async def test_aborts_404_for_invalid_format(app):
    async with app.test_request_context("/"):
        # No DB hit needed — format check rejects.
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_public_id_or_404("tt0393109")
        assert exc_info.value.code == 404
