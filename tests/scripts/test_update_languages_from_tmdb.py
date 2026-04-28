from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts import update_languages_from_tmdb as repair_script


@pytest.mark.asyncio
async def test_update_languages_refreshes_candidate_table_after_repairs(monkeypatch):
    db_pool = AsyncMock()
    db_pool.execute = AsyncMock(
        side_effect=[
            [
                {
                    "tconst": "tt1234567",
                    "primaryTitle": "Broken Language Movie",
                    "startYear": 2024,
                    "language": None,
                }
            ],
            1,
            [],
        ]
    )
    db_pool.init_pool = AsyncMock()
    db_pool.close_pool = AsyncMock()

    tmdb = AsyncMock()
    tmdb.get_tmdb_id_by_tconst.return_value = 77
    tmdb.get_movie_info_by_tmdb_id.return_value = {"original_language": "te"}
    tmdb.close = AsyncMock()

    candidate_store = MagicMock()
    candidate_store.refresh_movie_candidates = AsyncMock()

    from infra import pool as pool_module
    import settings as settings_module

    monkeypatch.setattr(pool_module, "DatabaseConnectionPool", lambda _config: db_pool)
    monkeypatch.setattr(
        settings_module,
        "Config",
        type("ConfigStub", (), {"get_db_config": staticmethod(lambda: {})}),
    )
    monkeypatch.setattr(repair_script, "TMDbHelper", lambda: tmdb)
    monkeypatch.setattr(
        repair_script,
        "CandidateStore",
        lambda pool: candidate_store,
        raising=False,
    )
    monkeypatch.setattr(repair_script.asyncio, "sleep", AsyncMock())

    await repair_script.update_languages(
        limit=10,
        min_votes=1000,
        max_votes=None,
        predicate=repair_script._BAD_LANGUAGE_PREDICATE,
    )

    candidate_store.refresh_movie_candidates.assert_awaited_once()
