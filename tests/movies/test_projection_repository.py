import pytest

from movies.projection_repository import ProjectionRepository


@pytest.mark.asyncio
async def test_upsert_failed_qualifies_existing_payload_json_in_aliased_upsert(mock_db_pool):
    repository = ProjectionRepository(mock_db_pool)

    await repository.upsert_failed(
        "tt1234567",
        {"title": "Fallback"},
        now="2026-04-09T15:48:47Z",
        attempts=2,
        error="TMDB enrichment returned no payload",
        tmdb_id=None,
    )

    mock_db_pool.execute.assert_awaited_once()
    sql = mock_db_pool.execute.await_args.args[0]

    assert "AS new_row" in sql
    assert "payload_json = COALESCE(movie_projection.payload_json, new_row.payload_json)" in sql


from unittest.mock import AsyncMock, patch

from movies.projection_repository import ProjectionRepository
from movies.projection_state import ProjectionState


async def test_upsert_ready_assigns_public_id():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    repo = ProjectionRepository(pool)

    payload = {
        "tconst": "tt0393109",
        "tmdb_id": 1234,
        "title": "The Departed",
        "year": "2006",
        "_full": True,
    }

    with patch("movies.projection_repository.assign_public_id", new=AsyncMock(return_value="a8fk3j")) as assigner:
        await repo.upsert_ready("tt0393109", payload, now=__import__("datetime").datetime(2026, 4, 26, 12, 0, 0), attempts=1)

    assigner.assert_awaited_once_with(pool, "tt0393109")


async def test_ensure_core_projection_assigns_public_id():
    pool = AsyncMock()
    # First execute is the title.basics SELECT; return a row.
    pool.execute = AsyncMock(side_effect=[
        {
            "tconst": "tt0393109",
            "primaryTitle": "The Departed",
            "startYear": 2006,
            "genres": "Crime,Drama",
            "language": "en",
            "slug": "the-departed-2006",
            "averageRating": 8.5,
            "numVotes": 100000,
        },
        None,  # INSERT ... ON DUPLICATE KEY UPDATE
    ])
    repo = ProjectionRepository(pool)

    with patch("movies.projection_repository.assign_public_id", new=AsyncMock(return_value="a8fk3j")) as assigner:
        result = await repo.ensure_core_projection("tt0393109")

    assert result is not None
    assigner.assert_awaited_once_with(pool, "tt0393109")


async def test_select_row_returns_public_id():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value={
        "tconst": "tt0393109",
        "tmdb_id": 1234,
        "payload_json": '{"title": "The Departed"}',
        "projection_state": "ready",
        "enriched_at": None,
        "stale_after": None,
        "last_attempt_at": None,
        "attempt_count": 0,
        "last_error": None,
        "public_id": "a8fk3j",
    })
    repo = ProjectionRepository(pool)

    row = await repo.select_row("tt0393109")

    sql = pool.execute.await_args[0][0]
    assert "public_id" in sql
    assert row["public_id"] == "a8fk3j"


def test_payload_from_row_carries_public_id():
    repo = ProjectionRepository(db_pool=None)
    payload = repo.payload_from_row({
        "tconst": "tt0393109",
        "payload_json": '{"title": "The Departed"}',
        "projection_state": "ready",
        "public_id": "a8fk3j",
    })
    assert payload["public_id"] == "a8fk3j"


def test_payload_from_row_omits_public_id_when_missing():
    repo = ProjectionRepository(db_pool=None)
    payload = repo.payload_from_row({
        "tconst": "tt0393109",
        "payload_json": '{"title": "The Departed"}',
        "projection_state": "ready",
    })
    # Absence is preserved as None so callers can distinguish "loaded
    # before backfill" from "explicitly empty".
    assert payload.get("public_id") is None


def test_build_core_payload_includes_public_id_field():
    repo = ProjectionRepository(db_pool=None)
    payload = repo.build_core_payload({
        "tconst": "tt0393109",
        "primaryTitle": "The Departed",
        "startYear": 2006,
        "genres": "Crime,Drama",
        "language": "en",
        "slug": "the-departed-2006",
        "averageRating": 8.5,
        "numVotes": 100000,
    })
    # Always present, even at CORE state — populated by the post-insert assign.
    assert "public_id" in payload
    assert payload["public_id"] is None  # not yet assigned at this point
