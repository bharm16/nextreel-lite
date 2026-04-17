"""Shared blueprint state and helpers for feature route modules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from quart import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)

from infra.time_utils import current_year as _current_year, env_bool, utcnow as _utcnow
from logging_config import get_logger
from nextreel.application.auth_flows import GoogleOAuthService, RegistrationService
from nextreel.application.letterboxd_import_service import LetterboxdImportService
from nextreel.application.movie_navigator import NavigationOutcome
from nextreel.application.watched_progress_service import WatchedEnrichmentProgressService
from nextreel.web.route_services import (
    MovieDetailService,
    WatchedListPresenter,
    WatchedMutationService,
)
from session import user_preferences

if TYPE_CHECKING:
    from infra.metrics import MetricsCollector
    from nextreel.application.movie_service import MovieManager

logger = get_logger(__name__)

bp = Blueprint("main", __name__)

_REQUEST_TIMEOUT = 30
_TCONST_RE = re.compile(r"^tt\d{1,10}$")
_TMDB_IMAGE_PREFIX = "https://image.tmdb.org/t/p/"

_AVATAR_PALETTE = (
    "#6366f1", "#8b5cf6", "#ec4899", "#f97316",
    "#eab308", "#22c55e", "#14b8a6", "#0ea5e9",
)


def user_avatar_info(user) -> dict:
    """Derive {initials, color} from a user dict/row for avatar rendering.

    Accepts a dict-like user record with optional ``display_name`` and
    ``email`` fields, plus ``user_id`` used to seed the background color.
    Always returns 1-2 uppercase initials and a palette color.
    """
    if not user:
        return {"initials": "?", "color": _AVATAR_PALETTE[0]}
    name = (user.get("display_name") or "").strip()
    if not name:
        email = user.get("email") or ""
        name = email.split("@", 1)[0] if email else ""
    parts = name.split()
    if len(parts) >= 2:
        initials = (parts[0][:1] + parts[-1][:1]).upper()
    else:
        initials = (name[:2] or "?").upper()
    seed = user.get("user_id") or name or ""
    bucket = sum(ord(ch) for ch in seed) % len(_AVATAR_PALETTE)
    return {"initials": initials, "color": _AVATAR_PALETTE[bucket]}

_registration_service = RegistrationService()
_google_oauth_service = GoogleOAuthService()
_movie_detail_service = MovieDetailService()
_watched_list_presenter = WatchedListPresenter()
_watched_mutation_service = WatchedMutationService()
_letterboxd_import_service = LetterboxdImportService()
_watched_progress_service = WatchedEnrichmentProgressService()


def _movie_detail_blocks_partial_render() -> bool:
    return env_bool("PROJECTION_ENRICHMENT_BLOCKS_RENDER", default=True)


def _tmdb_image_path(image_url: str | None) -> str | None:
    if not image_url or not isinstance(image_url, str):
        return None
    if image_url.startswith("/static/"):
        return None
    if image_url.startswith("/"):
        return image_url
    if not image_url.startswith(_TMDB_IMAGE_PREFIX):
        return None

    remainder = image_url[len(_TMDB_IMAGE_PREFIX) :]
    if "/" not in remainder:
        return None
    _size, path = remainder.split("/", 1)
    if not path:
        return None
    return "/" + path.lstrip("/")


def _tmdb_sized_image_url(image_url: str | None, *, size: str) -> str | None:
    path = _tmdb_image_path(image_url)
    if not path:
        return None
    return f"{_TMDB_IMAGE_PREFIX}{size}{path}"


def _movie_image_context(movie: dict) -> dict[str, str | None]:
    backdrop_url = movie.get("backdrop_url")
    poster_url = movie.get("poster_url") or "/static/img/poster-placeholder.svg"

    hero_image_url = _tmdb_sized_image_url(backdrop_url, size="w780") or backdrop_url or poster_url
    hero_path = _tmdb_image_path(backdrop_url)
    hero_image_srcset = None
    hero_image_sizes = None
    if hero_path:
        hero_image_srcset = (
            f"{_TMDB_IMAGE_PREFIX}w780{hero_path} 780w, "
            f"{_TMDB_IMAGE_PREFIX}w1280{hero_path} 1280w"
        )
        hero_image_sizes = "(max-width: 640px) 100vw, 42vw"

    return {
        "hero_image_url": hero_image_url,
        "hero_image_srcset": hero_image_srcset,
        "hero_image_sizes": hero_image_sizes,
        "preload_image_url": hero_image_url,
        "preload_image_srcset": hero_image_srcset,
    }


def _no_matches_response():
    """JSON 'no movies matched' response shared by /filtered_movie branches."""
    return jsonify(
        {
            "ok": False,
            "errors": {"form": "No movies matched your filters. Try broadening your criteria."},
        }
    )


def _wants_json_response() -> bool:
    return "application/json" in request.headers.get("Accept", "")


@dataclass(slots=True)
class NextReelServices:
    movie_manager: "MovieManager"
    metrics_collector: "MetricsCollector"


def init_routes(app, movie_manager, metrics_collector):
    app.extensions["nextreel"] = NextReelServices(
        movie_manager=movie_manager,
        metrics_collector=metrics_collector,
    )


def _services() -> NextReelServices:
    services = current_app.extensions.get("nextreel")
    if services is None:
        abort(503, description="Application services unavailable")
    return services


async def _schedule_prefetch(tconst: str) -> None:
    """Best-effort local prefetch for the redirect target."""
    try:
        services = _services()
        store = services.movie_manager.projection_store
        coordinator = store.coordinator
        if coordinator is None:
            return
        if not coordinator.tmdb_helper:
            return
        row = await store.select_row(tconst)
        if row and row.get("projection_state") == "ready":
            stale_after = row.get("stale_after")
            if not stale_after or stale_after > _utcnow():
                return
        tmdb_id = row.get("tmdb_id") if row else None
        await coordinator.get_or_start_inflight(tconst, tmdb_id=tmdb_id)
    except Exception as exc:  # pragma: no cover - best-effort only
        logger.debug("Prefetch scheduling skipped for %s: %s", tconst, exc)


async def _redirect_for_navigation_outcome(outcome: NavigationOutcome):
    if outcome.state_conflict:
        if outcome.tconst:
            return redirect(
                url_for("main.movie_detail", tconst=outcome.tconst, state_conflict=1),
                code=303,
            )
        return redirect(url_for("main.home", state_conflict=1), code=303)
    if outcome.tconst:
        await _schedule_prefetch(outcome.tconst)
        return redirect(url_for("main.movie_detail", tconst=outcome.tconst), code=303)
    abort(500, description="Navigation outcome missing target movie")


def _legacy_session():
    if current_app.config.get("SESSION_REDIS"):
        return session
    return None


def _current_state():
    state = getattr(g, "navigation_state", None)
    if state is None:
        abort(503, description="Navigation state unavailable")
    return state


def _get_csrf_token() -> str:
    return _current_state().csrf_token


def _current_user_id() -> str | None:
    """Return the user_id from the current navigation state, or None if anonymous."""
    state = getattr(g, "navigation_state", None)
    return getattr(state, "user_id", None) if state else None


def _require_login():
    """Return a redirect to login if the user is not authenticated, else None."""
    if not _current_user_id():
        return redirect(url_for("main.login_page"))
    return None


async def _attach_user_to_current_session(user_id: str):
    state = _current_state()
    services = _services()
    exclude_watched = await user_preferences.get_exclude_watched_default(
        services.movie_manager.db_pool,
        user_id,
    )
    updated_state = await current_app.navigation_state_store.bind_user(
        state,
        user_id,
        exclude_watched=exclude_watched,
    )
    if updated_state is None:
        abort(409, description="Could not bind authenticated user to navigation state")
    g.navigation_state = updated_state
    return updated_state


__all__ = [
    "NextReelServices",
    "_REQUEST_TIMEOUT",
    "_TCONST_RE",
    "_attach_user_to_current_session",
    "_current_state",
    "_current_user_id",
    "_get_csrf_token",
    "_google_oauth_service",
    "_legacy_session",
    "_letterboxd_import_service",
    "_movie_detail_blocks_partial_render",
    "_movie_detail_service",
    "_movie_image_context",
    "_no_matches_response",
    "_redirect_for_navigation_outcome",
    "_registration_service",
    "_require_login",
    "_services",
    "_wants_json_response",
    "_watched_list_presenter",
    "_watched_mutation_service",
    "_watched_progress_service",
    "_current_year",
    "bp",
    "init_routes",
    "logger",
]
