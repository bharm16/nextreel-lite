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
