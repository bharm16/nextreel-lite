"""Shared blueprint state and helpers for feature route modules."""

from __future__ import annotations

import asyncio
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

from infra.time_utils import current_year as _current_year, utcnow as _utcnow
from logging_config import get_logger
from movies.movie_url import build_movie_path
from movies.public_id import (
    ID_RE as _PUBLIC_ID_RE,
    public_id_for_tconst,
    resolve_to_tconst,
)
from nextreel.application.auth_flows import GoogleOAuthService, RegistrationService
from nextreel.application.letterboxd_import_service import LetterboxdImportService
from nextreel.application.movie_navigator import NavigationOutcome
from nextreel.application.watched_progress_service import WatchedEnrichmentProgressService
from nextreel.web.route_services import (
    MovieDetailService,
    WatchedListPresenter,
    WatchlistPresenter,
)
from session import user_preferences

if TYPE_CHECKING:
    from infra.metrics import MetricsCollector
    from nextreel.application.movie_service import MovieManager

logger = get_logger(__name__)

bp = Blueprint("main", __name__)

_REQUEST_TIMEOUT = 30
_TMDB_IMAGE_PREFIX = "https://image.tmdb.org/t/p/"

_AVATAR_PALETTE = (
    "#6366f1", "#8b5cf6", "#ec4899", "#f97316",
    "#eab308", "#22c55e", "#14b8a6", "#0ea5e9",
)

_LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "cn": "Chinese", "hi": "Hindi",
    "ar": "Arabic", "tr": "Turkish", "pl": "Polish", "nl": "Dutch",
    "sv": "Swedish", "no": "Norwegian", "nb": "Norwegian", "da": "Danish",
    "fi": "Finnish", "el": "Greek", "he": "Hebrew", "cs": "Czech",
    "ro": "Romanian", "hu": "Hungarian", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "uk": "Ukrainian", "bg": "Bulgarian", "hr": "Croatian",
    "sr": "Serbian", "sk": "Slovak", "sl": "Slovenian", "et": "Estonian",
    "lv": "Latvian", "lt": "Lithuanian", "ms": "Malay", "tl": "Tagalog",
    "fa": "Persian", "ur": "Urdu", "bn": "Bengali", "ta": "Tamil",
    "te": "Telugu", "ml": "Malayalam", "kn": "Kannada", "mr": "Marathi",
    "gu": "Gujarati", "pa": "Punjabi", "la": "Latin", "is": "Icelandic",
    "ga": "Irish", "cy": "Welsh", "ca": "Catalan", "eu": "Basque",
    "gl": "Galician", "af": "Afrikaans", "sw": "Swahili", "am": "Amharic",
    "yi": "Yiddish", "eo": "Esperanto", "xx": "No Language",
    # TMDb occasionally returns 3-letter codes for Chinese-language films.
    "cmn": "Mandarin", "yue": "Cantonese", "nan": "Min Nan", "wuu": "Wu",
}


def language_name(code: str | None) -> str:
    """Map an ISO 639-1 code to its English name; fall back to uppercase code."""
    if not code:
        return ""
    key = str(code).strip().lower()
    return _LANGUAGE_NAMES.get(key, key.upper())


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
_watchlist_list_presenter = WatchlistPresenter()
_letterboxd_import_service = LetterboxdImportService()
_watched_progress_service = WatchedEnrichmentProgressService()


# ── Shared list-page helpers ──────────────────────────────────────────
# Used by both /watched and /watchlist (parallel-sibling features).

LIST_VALID_SORTS = frozenset(
    {"recent", "title_asc", "title_desc", "year_desc", "rating_desc"}
)


def parse_list_pagination(args) -> tuple[int, int, int]:
    """Parse (page, per_page, offset) from a request's query args.

    Defaults: page=1, per_page=60, capped at [1, 200].
    """
    try:
        page = max(1, int(args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(args.get("per_page", 60))
    except (TypeError, ValueError):
        per_page = 60
    per_page = max(1, min(per_page, 200))
    offset = (page - 1) * per_page
    return page, per_page, offset


def parse_list_filter_params(args) -> dict:
    """Parse decade / rating / genre filter params from request query string."""
    result: dict = {}

    decades_raw = args.get("decades", "")
    if decades_raw:
        result["decades"] = [
            d.strip().rstrip("s") for d in decades_raw.split(",") if d.strip()
        ]

    rating_tier = args.get("rating", "")
    if rating_tier == "8+":
        result["rating_min"] = 8.0
        result["rating_max"] = 10.0
    elif rating_tier == "6-8":
        result["rating_min"] = 6.0
        result["rating_max"] = 7.99
    elif rating_tier == "<6":
        result["rating_min"] = 0.0
        result["rating_max"] = 5.99

    genres_raw = args.get("genres", "")
    if genres_raw:
        result["genres"] = [g.strip() for g in genres_raw.split(",") if g.strip()]

    return result


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


def _movie_url_global(movie: dict) -> str:
    """Jinja global: return the canonical /movie/... URL for a movie dict.

    Returns ``/`` if the dict is missing a public_id. The startup assertion
    in ``ensure_runtime_schema`` guarantees every projection row has a
    public_id, so reaching this fallback indicates a movie-dict producer
    that's silently dropping the field — log it so we can find the leak.
    """
    if not movie:
        logger.warning("movie_url() called with empty/None movie dict")
        return "/"
    public_id = movie.get("public_id")
    if not public_id:
        logger.warning(
            "movie_url() falling back to '/' — movie dict missing public_id; keys=%s",
            sorted(movie.keys()),
        )
        return "/"
    title = movie.get("primaryTitle") or movie.get("title")
    year = movie.get("year") or movie.get("startYear")
    return build_movie_path(title, year, public_id)


def init_routes(app, movie_manager, metrics_collector):
    app.extensions["nextreel"] = NextReelServices(
        movie_manager=movie_manager,
        metrics_collector=metrics_collector,
    )
    app.jinja_env.filters["language_name"] = language_name
    app.jinja_env.globals["movie_url"] = _movie_url_global


def _services() -> NextReelServices:
    services = current_app.extensions.get("nextreel")
    if services is None:
        abort(503, description="Application services unavailable")
    return services


async def _resolve_public_id_or_404(public_id: str) -> str:
    """Resolve a public_id from a route path to a tconst, or abort 404.

    Combines format validation and DB resolution so route handlers can
    write a single line to translate the URL identifier to the internal
    primary key.
    """
    if not isinstance(public_id, str) or not _PUBLIC_ID_RE.match(public_id):
        abort(404)
    services = _services()
    tconst = await resolve_to_tconst(services.movie_manager.db_pool, public_id)
    if tconst is None:
        abort(404)
    return tconst


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


async def _build_movie_url_for_tconst(tconst: str, *, query: dict | None = None) -> str:
    """Look up the projection row for ``tconst`` and build the canonical URL.

    Falls back to ``/`` if the projection has no public_id yet (a transient
    state during the rollout, after which assertion at startup guarantees
    every row has one).
    """
    services = _services()
    projection = await services.movie_manager.projection_store.select_row(tconst)
    if not projection:
        # Lazy-create the projection row (and its public_id) so the URL
        # builder has something to point to. Same lazy pattern the GET
        # route used to rely on via fetch_renderable_payload — we just
        # trigger it earlier so the redirect target is canonical. Without
        # this, navigation picks a candidate from movie_candidates that
        # has never been viewed, finds no projection row, and silently
        # redirects the user back to the landing page.
        try:
            await services.movie_manager.projection_store.ensure_core_projection(tconst)
        except Exception:
            logger.debug(
                "ensure_core_projection failed for %s", tconst, exc_info=True
            )
            return url_for("main.home")
        projection = await services.movie_manager.projection_store.select_row(tconst)
        if not projection:
            return url_for("main.home")
    public_id = projection.get("public_id")
    if not public_id:
        # Last-resort fallback during the rollout window.
        public_id = await public_id_for_tconst(services.movie_manager.db_pool, tconst)
        if not public_id:
            return url_for("main.home")

    payload = projection.get("payload_json")
    if isinstance(payload, str):
        import json as _json
        payload = _json.loads(payload) if payload else {}
    if not isinstance(payload, dict):
        payload = {}
    title = payload.get("primaryTitle") or payload.get("title")
    year = payload.get("year")
    path = build_movie_path(title, year, public_id)
    if query:
        from urllib.parse import urlencode
        path = f"{path}?{urlencode(query)}"
    return path


async def _build_movie_url_from_outcome(
    outcome: NavigationOutcome | None, *, query: dict | None = None
) -> str:
    """Build the canonical /movie/... URL from a NavigationOutcome.

    Hot path: outcome carries public_id + title + year (all populated by
    the navigator from the candidate ref) → build URL with zero DB hits.
    Cold path: any field missing (first-time candidate with no projection
    row, or a state_conflict path that only knows the tconst) → fall back
    to the DB-backed builder which lazy-creates the projection.
    """
    if outcome is None or outcome.tconst is None:
        return url_for("main.home")
    if outcome.public_id and outcome.title:
        path = build_movie_path(outcome.title, outcome.year, outcome.public_id)
        if query:
            from urllib.parse import urlencode
            path = f"{path}?{urlencode(query)}"
        return path
    return await _build_movie_url_for_tconst(outcome.tconst, query=query)


async def _redirect_for_navigation_outcome(outcome: NavigationOutcome):
    if outcome.state_conflict:
        if outcome.tconst:
            url = await _build_movie_url_from_outcome(
                outcome, query={"state_conflict": "1"}
            )
            return redirect(url, code=303)
        return redirect(url_for("main.home", state_conflict=1), code=303)
    if outcome.tconst:
        # Warm the just-chosen movie AND the next 1-2 candidates from the
        # navigator queue. The lookahead is what fixes back-to-back clicks
        # rendering the core placeholder — by the time the user clicks
        # again, those tconsts already have an in-flight (or completed)
        # TMDb fetch. _schedule_prefetch is best-effort and self-throttling
        # (early-exits on READY rows), so paying for 2 extra select_rows
        # per click is cheap. Run them concurrently so we don't add the
        # network latencies serially. ``return_exceptions=True`` is
        # intentional: prefetch is best-effort, and the actual enrichment
        # tasks are detached via the coordinator's background scheduler,
        # so a CancelledError here (e.g. client disconnect) does not kill
        # the work already kicked off.
        prefetch_targets = (outcome.tconst, *outcome.upcoming_tconsts)
        await asyncio.gather(
            *(_schedule_prefetch(t) for t in prefetch_targets),
            return_exceptions=True,
        )
        url = await _build_movie_url_from_outcome(outcome)
        return redirect(url, code=303)
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
        services.movie_manager.db_pool, user_id
    )
    exclude_watchlist = await user_preferences.get_exclude_watchlist_default(
        services.movie_manager.db_pool, user_id
    )
    updated_state = await current_app.navigation_state_store.bind_user(
        state,
        user_id,
        exclude_watched=exclude_watched,
        exclude_watchlist=exclude_watchlist,
    )
    if updated_state is None:
        abort(409, description="Could not bind authenticated user to navigation state")
    g.navigation_state = updated_state
    g.set_nr_sid_cookie = True
    return updated_state


__all__ = [
    "LIST_VALID_SORTS",
    "NextReelServices",
    "_PUBLIC_ID_RE",
    "_REQUEST_TIMEOUT",
    "_attach_user_to_current_session",
    "_build_movie_url_for_tconst",
    "_build_movie_url_from_outcome",
    "_current_state",
    "_current_user_id",
    "_get_csrf_token",
    "_google_oauth_service",
    "_legacy_session",
    "_letterboxd_import_service",
    "_movie_detail_service",
    "_movie_image_context",
    "_movie_url_global",
    "_no_matches_response",
    "_redirect_for_navigation_outcome",
    "_registration_service",
    "_require_login",
    "_resolve_public_id_or_404",
    "_services",
    "_wants_json_response",
    "_watched_list_presenter",
    "_watchlist_list_presenter",
    "_watched_progress_service",
    "_current_year",
    "bp",
    "init_routes",
    "logger",
    "parse_list_filter_params",
    "parse_list_pagination",
]
