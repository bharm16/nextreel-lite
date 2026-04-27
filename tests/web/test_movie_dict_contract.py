"""Guards the contract that movie dicts handed to templates carry public_id.

Missing this field would yield a silently-broken link in templates (the
``movie_url`` Jinja global returns ``/`` when public_id is absent), with
no runtime error to surface the bug. This test asserts the contract at
each producer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


REQUIRED_KEYS = {"public_id", "primaryTitle"}


def _assert_movie_dict(d: dict, *, where: str) -> None:
    missing = REQUIRED_KEYS - d.keys()
    assert not missing, f"{where} produced a movie dict missing keys: {missing}"


async def test_route_services_movie_detail_view_model_contract():
    from nextreel.web.route_services import MovieDetailService
    from types import SimpleNamespace

    payload = {
        "tconst": "tt0393109",
        "title": "The Departed",
        "primaryTitle": "The Departed",
        "year": "2006",
        "public_id": "a8fk3j",
        "_full": True,
    }
    movie_manager = SimpleNamespace(
        projection_store=SimpleNamespace(
            fetch_renderable_payload=AsyncMock(return_value=payload)
        ),
        watched_store=SimpleNamespace(is_watched=AsyncMock(return_value=False)),
        watchlist_store=SimpleNamespace(is_in_watchlist=AsyncMock(return_value=False)),
        prev_stack_length=lambda state: 0,
    )
    vm = await MovieDetailService().get(
        movie_manager=movie_manager,
        state=SimpleNamespace(),
        user_id=None,
        tconst="tt0393109",
    )
    _assert_movie_dict(vm.movie, where="MovieDetailService")


def test_projection_repository_payload_from_row_contract():
    from movies.projection_repository import ProjectionRepository
    repo = ProjectionRepository(db_pool=None)
    payload = repo.payload_from_row({
        "tconst": "tt0393109",
        "payload_json": '{"primaryTitle": "The Departed"}',
        "projection_state": "ready",
        "public_id": "a8fk3j",
    })
    _assert_movie_dict(payload, where="ProjectionRepository.payload_from_row")


def test_navigator_movie_ref_carries_public_id():
    from nextreel.application.movie_navigator import _movie_ref
    ref = _movie_ref({
        "tconst": "tt0393109",
        "title": "The Departed",
        "primaryTitle": "The Departed",
        "public_id": "a8fk3j",
    })
    # _movie_ref is the lightweight shape — it must include public_id so
    # downstream URL building doesn't lose the canonical key.
    assert ref.get("public_id") == "a8fk3j"


# ---------------------------------------------------------------------------
# Additional producers
# ---------------------------------------------------------------------------


async def test_landing_film_service_carries_public_id():
    """The landing-hero dict must carry public_id so movie_url() resolves.

    Note: the landing-film payload uses ``title`` (not ``primaryTitle``)
    — the home template renders ``landing_film.title`` directly and
    ``movie_url`` falls back from ``primaryTitle`` to ``title``. So we
    only assert the load-bearing key here: public_id. If the producer
    is ever generalised to feed shared template partials, expand this
    to the full REQUIRED_KEYS contract.
    """
    from movies import landing_film_service
    from movies.landing_film_service import fetch_random_landing_film

    landing_film_service._reset_ready_count_cache()

    pool = AsyncMock()
    pool.execute = AsyncMock(side_effect=[
        # _ready_row_count -> SELECT COUNT(*)
        {"n": 1},
        # id-only SELECT tconst ... LIMIT/OFFSET
        [{"tconst": "tt0393109"}],
        # payload SELECT tconst, payload_json, public_id ...
        [
            {
                "tconst": "tt0393109",
                "public_id": "a8fk3j",
                "payload_json": (
                    '{"title": "The Departed", "year": "2006",'
                    ' "directors": "Martin Scorsese", "runtime": "151 min",'
                    ' "backdrop_url": "https://image.tmdb.org/t/p/original/x.jpg"}'
                ),
            }
        ],
    ])

    result = await fetch_random_landing_film(pool)
    landing_film_service._reset_ready_count_cache()

    assert result is not None, "landing-film should return a dict for a ready row"
    assert result.get("public_id") == "a8fk3j", (
        "landing-film must include public_id — without it, movie_url() falls back to '/'"
    )
    assert result.get("title") == "The Departed"


async def test_watched_store_list_filtered_carries_public_id():
    """list_watched_filtered rows must include public_id and primaryTitle.

    Watched-list templates resolve movie URLs from these rows via the
    presenter; both fields must reach the row dict so neither the SQL
    SELECT nor a future projection schema change can silently drop them.
    """
    from movies.watched_store import WatchedStore

    rows = [
        {
            "tconst": "tt0393109",
            "watched_at": "2024-01-01T00:00:00",
            "primaryTitle": "The Departed",
            "startYear": 2006,
            "genres": "Crime,Drama,Thriller",
            "slug": "the-departed",
            "averageRating": 8.5,
            "payload_json": '{"title": "The Departed"}',
            "public_id": "a8fk3j",
        }
    ]
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=rows)

    store = WatchedStore(db_pool=pool)
    result = await store.list_watched_filtered(user_id="user-1")

    assert result, "list_watched_filtered should return rows"
    _assert_movie_dict(result[0], where="WatchedStore.list_watched_filtered")
    assert result[0]["public_id"] == "a8fk3j"
    assert result[0]["primaryTitle"] == "The Departed"


async def test_watchlist_store_list_filtered_carries_public_id():
    """list_watchlist_filtered rows must include public_id and primaryTitle."""
    from movies.watchlist_store import WatchlistStore

    rows = [
        {
            "tconst": "tt0393109",
            "added_at": "2024-01-01T00:00:00",
            "primaryTitle": "The Departed",
            "startYear": 2006,
            "genres": "Crime,Drama,Thriller",
            "slug": "the-departed",
            "averageRating": 8.5,
            "payload_json": '{"title": "The Departed"}',
            "public_id": "a8fk3j",
        }
    ]
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=rows)

    store = WatchlistStore(db_pool=pool)
    result = await store.list_watchlist_filtered(user_id="user-1")

    assert result, "list_watchlist_filtered should return rows"
    _assert_movie_dict(result[0], where="WatchlistStore.list_watchlist_filtered")
    assert result[0]["public_id"] == "a8fk3j"
    assert result[0]["primaryTitle"] == "The Departed"
