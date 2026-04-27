"""Route tests for /watchlist endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quart import g
from werkzeug.exceptions import HTTPException

import nextreel.web.routes.watchlist as watchlist_routes
from nextreel.web.routes.shared import NextReelServices


def _nav_state(user_id: str | None = "user-123") -> SimpleNamespace:
    return SimpleNamespace(csrf_token="csrf-token", user_id=user_id)


def _install_services(app):
    watchlist_store = MagicMock()
    watchlist_store.add = AsyncMock()
    watchlist_store.remove = AsyncMock()
    watchlist_store.is_in_watchlist = AsyncMock(return_value=False)
    watchlist_store.list_watchlist_filtered = AsyncMock(return_value=[])
    watchlist_store.count_filtered = AsyncMock(return_value=0)
    watchlist_store.available_filter_chips = AsyncMock(
        return_value={"decades": [], "genres": [], "ratings": []}
    )
    movie_manager = SimpleNamespace(db_pool=AsyncMock(), watchlist_store=watchlist_store)
    app.extensions["nextreel"] = NextReelServices(
        movie_manager=movie_manager,
        metrics_collector=MagicMock(),
    )
    return movie_manager, watchlist_store


@pytest.mark.asyncio
async def test_watchlist_list_redirects_when_not_logged_in(app, monkeypatch):
    _install_services(app)
    monkeypatch.setattr(
        "nextreel.web.routes.shared.url_for", lambda endpoint: "/login"
    )
    async with app.test_request_context("/watchlist"):
        g.navigation_state = _nav_state(user_id=None)
        response = await watchlist_routes.watchlist_page()
        # _require_login returns a redirect Response.
        assert response.status_code in (302, 303)


@pytest.mark.asyncio
async def test_add_to_watchlist_returns_json_when_requested(app):
    _, store = _install_services(app)
    async with app.test_request_context(
        "/watchlist/add/a8fk3j",
        method="POST",
        headers={"Accept": "application/json", "X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        with patch(
            "nextreel.web.routes.shared.resolve_to_tconst",
            new=AsyncMock(return_value="tt1234567"),
        ):
            response = await watchlist_routes.add_to_watchlist("a8fk3j")

    payload = await response.get_json()
    # tconst intentionally omitted from response — opaque public_id only.
    assert payload == {
        "ok": True,
        "is_in_watchlist": True,
        "public_id": "a8fk3j",
    }
    store.add.assert_awaited_once_with("user-123", "tt1234567")


@pytest.mark.asyncio
async def test_remove_from_watchlist_returns_json_when_requested(app):
    _, store = _install_services(app)
    async with app.test_request_context(
        "/watchlist/remove/a8fk3j",
        method="POST",
        headers={"Accept": "application/json", "X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        with patch(
            "nextreel.web.routes.shared.resolve_to_tconst",
            new=AsyncMock(return_value="tt1234567"),
        ):
            response = await watchlist_routes.remove_from_watchlist("a8fk3j")

    payload = await response.get_json()
    assert payload == {
        "ok": True,
        "is_in_watchlist": False,
        "public_id": "a8fk3j",
    }
    store.remove.assert_awaited_once_with("user-123", "tt1234567")


@pytest.mark.asyncio
async def test_add_rejects_invalid_public_id(app):
    _install_services(app)
    async with app.test_request_context(
        "/watchlist/add/not-a-public-id!",
        method="POST",
        headers={"X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        with pytest.raises(HTTPException) as exc_info:
            await watchlist_routes.add_to_watchlist("not-a-public-id!")
    assert exc_info.value.code == 404


def test_add_to_watchlist_is_csrf_and_rate_limit_decorated():
    """Sanity-check that the decorator stack is actually present.

    ``functools.wraps`` chains the decorators so we can walk back to the raw
    handler. Two layers (csrf_required + rate_limited) means two wrappers.
    """
    fn = watchlist_routes.add_to_watchlist
    assert hasattr(fn, "__wrapped__"), "@rate_limited not applied to add_to_watchlist"
    assert hasattr(fn.__wrapped__, "__wrapped__"), "@csrf_required not applied"


def test_remove_from_watchlist_is_csrf_and_rate_limit_decorated():
    fn = watchlist_routes.remove_from_watchlist
    assert hasattr(fn, "__wrapped__")
    assert hasattr(fn.__wrapped__, "__wrapped__")


@pytest.mark.asyncio
async def test_add_to_watchlist_rejects_request_without_csrf_token(app):
    """Through the @csrf_required wrapper: missing token → 403 abort."""
    _install_services(app)
    async with app.test_request_context(
        "/watchlist/add/tt1234567",
        method="POST",
        # No X-CSRFToken header, no form csrf_token field.
    ):
        g.navigation_state = _nav_state()
        with pytest.raises(HTTPException) as exc_info:
            # NB: __wrapped__ would skip the decorators; calling fn directly
            # exercises csrf_required first.
            await watchlist_routes.add_to_watchlist("tt1234567")
    assert exc_info.value.code == 403


@pytest.mark.asyncio
async def test_remove_from_watchlist_rejects_request_without_csrf_token(app):
    _install_services(app)
    async with app.test_request_context(
        "/watchlist/remove/tt1234567",
        method="POST",
    ):
        g.navigation_state = _nav_state()
        with pytest.raises(HTTPException) as exc_info:
            await watchlist_routes.remove_from_watchlist("tt1234567")
    assert exc_info.value.code == 403


@pytest.mark.asyncio
async def test_add_to_watchlist_returns_429_when_rate_limited(app):
    """Through the @rate_limited('watchlist') wrapper: bucket exhausted → 429."""
    _, store = _install_services(app)
    async with app.test_request_context(
        "/watchlist/add/tt1234567",
        method="POST",
        headers={"X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        with patch(
            "infra.route_helpers.check_rate_limit",
            AsyncMock(return_value=False),
        ) as check:
            response = await watchlist_routes.add_to_watchlist("tt1234567")

    payload, status = response
    assert status == 429
    assert payload == {"error": "rate limited"}
    check.assert_awaited_once_with("watchlist")
    store.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_from_watchlist_returns_429_when_rate_limited(app):
    _, store = _install_services(app)
    async with app.test_request_context(
        "/watchlist/remove/tt1234567",
        method="POST",
        headers={"X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        with patch(
            "infra.route_helpers.check_rate_limit",
            AsyncMock(return_value=False),
        ) as check:
            response = await watchlist_routes.remove_from_watchlist("tt1234567")

    _payload, status = response
    assert status == 429
    check.assert_awaited_once_with("watchlist")
    store.remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchlist_page_renders_for_logged_in_user(app, monkeypatch):
    """Empty watchlist renders HTML (smoke test for template + route wiring)."""
    _install_services(app)
    render_template = AsyncMock(return_value="<h1>Your watchlist is empty</h1>")
    monkeypatch.setattr(watchlist_routes, "render_template", render_template)
    async with app.test_request_context("/watchlist"):
        g.navigation_state = _nav_state()
        response = await watchlist_routes.watchlist_page()
    body = await response.get_data(as_text=True) if hasattr(response, "get_data") else response
    assert "Your watchlist is empty" in body or "Watchlist" in body
    render_template.assert_awaited_once()
    call_args = render_template.call_args
    assert call_args[0][0] == "watchlist.html"


@pytest.mark.asyncio
async def test_add_to_watchlist_resolves_public_id(app):
    """POST /watchlist/add/<public_id> resolves the ID and inserts the right tconst."""
    _movie_manager, watchlist_store = _install_services(app)
    watchlist_store.add = AsyncMock()

    async with app.test_request_context(
        "/watchlist/add/a8fk3j",
        method="POST",
        headers={"X-CSRFToken": "csrf-token", "Accept": "application/json"},
    ):
        g.navigation_state = _nav_state()
        with patch(
            "nextreel.web.routes.shared.resolve_to_tconst",
            new=AsyncMock(return_value="tt0393109"),
        ):
            response = await watchlist_routes.add_to_watchlist("a8fk3j")
            data = await response.get_json()

    assert response.status_code == 200
    assert data == {
        "ok": True,
        "is_in_watchlist": True,
        "public_id": "a8fk3j",
    }
    watchlist_store.add.assert_awaited_once_with("user-123", "tt0393109")


@pytest.mark.asyncio
async def test_remove_from_watchlist_resolves_public_id(app):
    """POST /watchlist/remove/<public_id> resolves the ID and removes the right tconst."""
    _movie_manager, watchlist_store = _install_services(app)
    watchlist_store.remove = AsyncMock()

    async with app.test_request_context(
        "/watchlist/remove/a8fk3j",
        method="POST",
        headers={"X-CSRFToken": "csrf-token", "Accept": "application/json"},
    ):
        g.navigation_state = _nav_state()
        with patch(
            "nextreel.web.routes.shared.resolve_to_tconst",
            new=AsyncMock(return_value="tt0393109"),
        ):
            response = await watchlist_routes.remove_from_watchlist("a8fk3j")
            data = await response.get_json()

    assert response.status_code == 200
    assert data == {
        "ok": True,
        "is_in_watchlist": False,
        "public_id": "a8fk3j",
    }
    watchlist_store.remove.assert_awaited_once_with("user-123", "tt0393109")


@pytest.mark.asyncio
async def test_add_to_watchlist_404_for_imdb_path(app):
    """Old /watchlist/add/tt0393109 path returns 404."""
    _install_services(app)

    async with app.test_request_context(
        "/watchlist/add/tt0393109",
        method="POST",
        headers={"X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        with pytest.raises(HTTPException) as exc_info:
            await watchlist_routes.add_to_watchlist("tt0393109")

    assert exc_info.value.code == 404


@pytest.mark.asyncio
async def test_remove_from_watchlist_404_for_imdb_path(app):
    """Old /watchlist/remove/tt0393109 path returns 404."""
    _install_services(app)

    async with app.test_request_context(
        "/watchlist/remove/tt0393109",
        method="POST",
        headers={"X-CSRFToken": "csrf-token"},
    ):
        g.navigation_state = _nav_state()
        with pytest.raises(HTTPException) as exc_info:
            await watchlist_routes.remove_from_watchlist("tt0393109")

    assert exc_info.value.code == 404
