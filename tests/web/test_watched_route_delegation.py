from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from quart import g, session as quart_session

from nextreel.application.letterboxd_import_service import LetterboxdImportOutcome
from nextreel.application.watched_progress_service import WatchedEnrichmentProgress
from nextreel.web.routes.shared import NextReelServices
import nextreel.web.routes.watched as watched_routes


class _UploadedFile:
    filename = "watched.csv"

    def __init__(self, content: bytes = b"Name,Year\nFilm,2024\n") -> None:
        self.stream = content


class _AwaitableFiles(dict):
    def __await__(self):
        async def _done():
            return self

        return _done().__await__()


def _nav_state(user_id: str | None = "user-123") -> SimpleNamespace:
    return SimpleNamespace(csrf_token="csrf-token", user_id=user_id)


def _install_services(app) -> tuple[SimpleNamespace, MagicMock]:
    watched_store = MagicMock()
    movie_manager = SimpleNamespace(db_pool=AsyncMock(), watched_store=watched_store)
    app.extensions["nextreel"] = NextReelServices(
        movie_manager=movie_manager,
        metrics_collector=MagicMock(),
    )
    return movie_manager, watched_store


@pytest.mark.asyncio
async def test_import_letterboxd_delegates_workflow_to_application_service(app, monkeypatch):
    movie_manager, watched_store = _install_services(app)
    import_watched = AsyncMock(
        return_value=LetterboxdImportOutcome(
            kind="success",
            flash_message="Imported 2 films. 1 could not be matched.",
            flash_category="success",
            matched=["tt1", "tt2"],
            unmatched_labels=["Missing (2024)"],
            enrichment_requested=True,
        )
    )
    monkeypatch.setattr(
        watched_routes._letterboxd_import_service,
        "import_watched",
        import_watched,
    )
    uploaded = _UploadedFile()
    monkeypatch.setattr(
        watched_routes,
        "request",
        SimpleNamespace(files=_AwaitableFiles({"letterboxd_csv": uploaded})),
    )
    monkeypatch.setattr(watched_routes, "url_for", lambda endpoint: "/watched")

    async with app.test_request_context(
        "/watched/import-letterboxd",
        method="POST",
        headers={"X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()

        response = await watched_routes.import_letterboxd()

        assert response.status_code == 302
        assert response.location == "/watched"
        import_watched.assert_awaited_once()
        _, kwargs = import_watched.await_args
        assert kwargs["user_id"] == "user-123"
        assert kwargs["db_pool"] is movie_manager.db_pool
        assert kwargs["watched_store"] is watched_store
        assert kwargs["uploaded"] is uploaded
        assert quart_session["letterboxd_import_tconsts"] == ["tt1", "tt2"]
        assert quart_session["letterboxd_enrichment_pending"] is True
        assert quart_session["letterboxd_sent_tconsts"] == []
        assert quart_session["letterboxd_unmatched"] == ["Missing (2024)"]
        flashed = quart_session["_flashes"]
        assert flashed == [("success", "Imported 2 films. 1 could not be matched.")]


@pytest.mark.asyncio
async def test_import_letterboxd_does_not_mutate_import_session_for_error_outcome(app, monkeypatch):
    _install_services(app)
    import_watched = AsyncMock(
        return_value=LetterboxdImportOutcome(
            kind="invalid_csv",
            flash_message="Invalid CSV format: Missing required column: Name.",
            flash_category="error",
        )
    )
    monkeypatch.setattr(
        watched_routes._letterboxd_import_service,
        "import_watched",
        import_watched,
    )
    monkeypatch.setattr(
        watched_routes,
        "request",
        SimpleNamespace(files=_AwaitableFiles({"letterboxd_csv": _UploadedFile(b"bad")})),
    )
    monkeypatch.setattr(watched_routes, "url_for", lambda endpoint: "/watched")

    async with app.test_request_context(
        "/watched/import-letterboxd",
        method="POST",
        headers={"X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()

        response = await watched_routes.import_letterboxd()

        assert response.status_code == 302
        assert "letterboxd_import_tconsts" not in quart_session
        assert "letterboxd_enrichment_pending" not in quart_session
        assert "letterboxd_sent_tconsts" not in quart_session
        assert "letterboxd_unmatched" not in quart_session
        assert quart_session["_flashes"] == [
            ("error", "Invalid CSV format: Missing required column: Name.")
        ]


@pytest.mark.asyncio
async def test_enrichment_progress_delegates_polling_to_application_service(app, monkeypatch):
    _install_services(app)
    progress = WatchedEnrichmentProgress(
        new_movies=[{"tconst": "tt1", "title": "Ready"}],
        new_count=1,
        total_ready=1,
        total=2,
        done=False,
    )
    service_progress = AsyncMock(return_value=progress)
    monkeypatch.setattr(
        watched_routes._watched_progress_service,
        "progress",
        service_progress,
    )
    render_template = AsyncMock(return_value="<article>Ready</article>")
    monkeypatch.setattr(watched_routes, "render_template", render_template)

    async with app.test_request_context("/watched/enrichment-progress"):
        g.navigation_state = _nav_state()
        quart_session["letterboxd_import_tconsts"] = ["tt1", "tt2"]
        quart_session["letterboxd_sent_tconsts"] = []
        quart_session["letterboxd_enrichment_pending"] = True

        response = await watched_routes.enrichment_progress()
        payload = await response.get_json()

        assert payload == {
            "html": "<article>Ready</article>",
            "new_count": 1,
            "total_ready": 1,
            "total": 2,
            "done": False,
        }
        service_progress.assert_awaited_once()
        _, kwargs = service_progress.await_args
        assert kwargs["session_state"] is quart_session
        assert kwargs["user_id"] == "user-123"
        assert kwargs["watched_store"] is app.extensions["nextreel"].movie_manager.watched_store
        assert kwargs["presenter"] is watched_routes._watched_list_presenter
        render_template.assert_awaited_once_with(
            "_watched_card.html",
            movie={"tconst": "tt1", "title": "Ready"},
        )
