"""Application route handlers."""

import asyncio
import hmac as _hmac
import re
import secrets as stdlib_secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from auth_flows import GoogleOAuthService, RegistrationService
from quart import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from filter_contracts import FilterState
from infra.metrics import user_actions_total
from infra.navigation_state import normalize_filters, validate_filters
from infra.ops_auth import check_ops_auth
from infra.rate_limit import check_rate_limit, get_rate_limit_backend
from infra.route_helpers import (
    csrf_required,
    rate_limited,
    safe_referrer as _safe_referrer,
    validate_csrf,
    with_timeout,
)
from infra.time_utils import current_year as _current_year, env_bool, utcnow as _utcnow
from logging_config import get_logger
from movie_navigator import NavigationOutcome
from route_services import MovieDetailService, WatchedListPresenter, WatchedMutationService
from session.keys import SESSION_OAUTH_STATE_KEY

# NOTE: Email/password auth remains a lazy in-function import so routes.py
# stays importable even when auth-only runtime dependencies are missing.

if TYPE_CHECKING:
    from infra.metrics import MetricsCollector
    from movie_service import MovieManager

logger = get_logger(__name__)

bp = Blueprint("main", __name__)

_REQUEST_TIMEOUT = 30
_TCONST_RE = re.compile(r"^tt\d{1,10}$")
_TMDB_IMAGE_PREFIX = "https://image.tmdb.org/t/p/"

# /ready response cache: collapses bursts of k8s/LB probes onto a single
# set of DB round-trips. 5s stays responsive to real state changes but
# absorbs typical probe fan-out (k8s + LB + Prometheus within one window).
_READY_CACHE_TTL_SECONDS = 5.0
_ready_cache_entry: tuple[float, tuple[dict, int]] | None = None
_ready_cache_lock = asyncio.Lock()
_registration_service = RegistrationService()
_google_oauth_service = GoogleOAuthService()
_movie_detail_service = MovieDetailService()
_watched_list_presenter = WatchedListPresenter()
_watched_mutation_service = WatchedMutationService()


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

    remainder = image_url[len(_TMDB_IMAGE_PREFIX):]
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

    hero_image_url = (
        _tmdb_sized_image_url(backdrop_url, size="w780")
        or backdrop_url
        or poster_url
    )
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
    return jsonify({
        "ok": False,
        "errors": {"form": "No movies matched your filters. Try broadening your criteria."},
    })


def _wants_json_response() -> bool:
    return "application/json" in request.headers.get("Accept", "")


async def _oauth_fail(flash_msg: str):
    """Flash an OAuth error and redirect to the login page."""
    await flash(flash_msg, "error")
    return redirect(url_for("main.login_page"))


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
    """Best-effort local prefetch for the redirect target.

    Inserts an in-flight enrichment task into the coordinator map
    *synchronously* before the redirect response is returned, so the
    detail request that immediately follows the redirect observes the
    in-flight task and reuses its result instead of starting duplicate
    enrichment.
    """
    try:
        services = _services()
        store = services.movie_manager.projection_store
        coordinator = store.coordinator
        if coordinator is None:
            return
        # Only schedule when we have the tools for enrichment.
        if not coordinator.tmdb_helper:
            return
        row = await store.select_row(tconst)
        # Skip when the row is already ready/fresh — no prefetch needed.
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


async def _render_filters_page(
    current_filters,
    *,
    validation_errors: dict[str, str] | None = None,
    form_notice: str | None = None,
    genres_notice: str | None = None,
    status_code: int = 200,
):
    response = await render_template(
        "set_filters.html",
        current_filters=current_filters,
        current_year=_current_year(),
        validation_errors=validation_errors or {},
        form_notice=form_notice,
        genres_notice=genres_notice,
    )
    return response, status_code


@bp.app_context_processor
def inject_csrf_token():
    state = getattr(g, "navigation_state", None)
    user_id = getattr(state, "user_id", None) if state else None
    oauth_config = getattr(current_app, "oauth_config", {})
    return {
        "csrf_token": _get_csrf_token,
        "current_user_id": user_id,
        "current_filters": (getattr(state, "filters", None) or {}),
        "current_year": _current_year(),
        "is_watched": getattr(g, "is_watched", False),
        "google_enabled": oauth_config.get("google_enabled", False),
        "apple_enabled": oauth_config.get("apple_enabled", False),
    }


@bp.route("/login")
async def login_page():
    if _current_user_id():
        return redirect(url_for("main.home"))
    return await render_template("login.html", errors={})


@bp.route("/login", methods=["POST"])
@csrf_required
@rate_limited("login")
async def login_submit():
    from session.user_auth import (
        EMAIL_PASSWORD_AUTH_UNAVAILABLE_MESSAGE,
        EmailPasswordAuthUnavailableError,
        authenticate_user,
    )

    form_data = await request.form
    email = form_data.get("email", "").strip()
    password = form_data.get("password", "")

    services = _services()
    try:
        user_id = await authenticate_user(services.movie_manager.db_pool, email, password)
    except EmailPasswordAuthUnavailableError:
        logger.warning("Email/password login unavailable: bcrypt dependency missing")
        return (
            await render_template(
                "login.html",
                errors={"form": EMAIL_PASSWORD_AUTH_UNAVAILABLE_MESSAGE},
            ),
            503,
        )

    if not user_id:
        return (
            await render_template("login.html", errors={"form": "Invalid email or password."}),
            401,
        )

    state = _current_state()
    await current_app.navigation_state_store.set_user_id(state.session_id, user_id)
    state.user_id = user_id
    logger.info("User %s logged in, session %s", user_id, state.session_id)
    return redirect(url_for("main.home"), code=303)


@bp.route("/register")
async def register_page():
    if _current_user_id():
        return redirect(url_for("main.home"))
    return await render_template("register.html", errors={})


@bp.route("/register", methods=["POST"])
@csrf_required
@rate_limited("register")
async def register_submit():
    form_data = await request.form
    email = form_data.get("email", "").strip()
    password = form_data.get("password", "")
    confirm_password = form_data.get("confirm_password", "")
    display_name = form_data.get("display_name", "").strip() or None

    services = _services()
    outcome = await _registration_service.register_email_user(
        email=email,
        password=password,
        confirm_password=confirm_password,
        display_name=display_name,
        db_pool=services.movie_manager.db_pool,
    )
    if outcome.kind != "success":
        if outcome.kind == "service_unavailable":
            logger.warning("Email/password registration unavailable: bcrypt dependency missing")
        status_code = 503 if outcome.kind == "service_unavailable" else 400
        return await render_template("register.html", errors=outcome.errors), status_code

    state = _current_state()
    user_id = outcome.user_id
    await current_app.navigation_state_store.set_user_id(state.session_id, user_id)
    state.user_id = user_id
    logger.info("User %s registered, session %s", user_id, state.session_id)
    return redirect(url_for("main.home"), code=303)


@bp.route("/logout", methods=["POST"])
@csrf_required
async def logout():
    state = _current_state()
    if state.user_id:
        await current_app.navigation_state_store.set_user_id(state.session_id, None)
        state.user_id = None
        logger.info("User logged out, session %s", state.session_id)
    response = redirect(url_for("main.home"), code=303)
    return response


@bp.route("/health")
async def health_check():
    if not await check_rate_limit("health"):
        return {"error": "rate limited"}, 429
    return {"status": "healthy"}, 200


@bp.route("/metrics")
async def metrics():
    if not check_ops_auth():
        return {"error": "unauthorized"}, 401
    if not await check_rate_limit("metrics"):
        return {"error": "rate limited"}, 429
    from infra.metrics import metrics_endpoint

    return await metrics_endpoint()


async def _compute_readiness(movie_manager) -> tuple[dict, int]:
    """Run all readiness sub-checks and build the response body.

    Split out so the cached wrapper can call it under the single-flight
    lock without duplicating the logic.
    """
    pool_metrics = await movie_manager.db_pool.get_metrics()
    if pool_metrics["circuit_breaker_state"] == "open":
        return {"status": "not_ready", "reason": "database_circuit_breaker_open"}, 503

    navigation_ready = await current_app.navigation_state_store.ready_check()
    candidates_fresh = await movie_manager.candidate_store.has_fresh_data()
    projection_ready = await movie_manager.projection_store.ready_check()
    status_code = 200 if navigation_ready and candidates_fresh and projection_ready else 503

    body = {
        "status": "ready" if status_code == 200 else "not_ready",
        "database": {
            "pool_size": pool_metrics["pool_size"],
            "free_connections": pool_metrics["free_connections"],
            "circuit_breaker_state": pool_metrics["circuit_breaker_state"],
            "queries_executed": pool_metrics["queries_executed"],
            "avg_query_time_ms": pool_metrics.get("avg_query_time_ms", 0),
        },
        "navigation_state": {
            "ready": navigation_ready,
        },
        "movie_candidates": {
            "fresh": candidates_fresh,
        },
        "projection_generation": {
            "ready": projection_ready,
        },
        "degraded": {
            "redis": "available" if current_app.redis_available else "unavailable",
            "worker": "available" if current_app.worker_available else "unavailable",
            "rate_limiter_backend": get_rate_limit_backend(),
        },
    }
    return body, status_code


@bp.route("/ready")
async def readiness_check():
    movie_manager = _services().movie_manager
    if not check_ops_auth():
        return {"error": "unauthorized"}, 401
    if not await check_rate_limit("ready"):
        return {"error": "rate limited"}, 429

    # Cache failures too — otherwise a failing check becomes a self-DOS
    # as every probe re-runs the expensive DB round-trips.
    global _ready_cache_entry
    cached = _ready_cache_entry
    if cached and time.time() - cached[0] < _READY_CACHE_TTL_SECONDS:
        return cached[1]

    async with _ready_cache_lock:
        cached = _ready_cache_entry
        if cached and time.time() - cached[0] < _READY_CACHE_TTL_SECONDS:
            return cached[1]
        try:
            result = await _compute_readiness(movie_manager)
        except Exception as exc:
            logger.error("Readiness check failed: %s", exc)
            result = ({"status": "not_ready", "reason": "internal_error"}, 503)
        _ready_cache_entry = (time.time(), result)
        return result


@bp.route("/movie/<tconst>")
@with_timeout(_REQUEST_TIMEOUT)
async def movie_detail(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")

    state = _current_state()
    user_id = _current_user_id()
    services = _services()
    movie_manager = services.movie_manager

    logger.debug(
        "Fetching movie details for tconst: %s, session_id: %s. Correlation ID: %s",
        tconst,
        state.session_id,
        g.correlation_id,
    )

    view_model = await _movie_detail_service.get(
        movie_manager=movie_manager,
        state=state,
        user_id=user_id,
        tconst=tconst,
    )

    if view_model is None:
        logger.info("No data found for movie with tconst: %s", tconst)
        return "Movie not found", 404

    if _movie_detail_blocks_partial_render() and not view_model.movie.get("_full"):
        logger.error(
            "Blocking partial movie detail render for %s (projection_state=%s)",
            tconst,
            view_model.movie.get("projection_state"),
        )
        return "Service temporarily unavailable", 503

    g.is_watched = view_model.is_watched
    image_context = _movie_image_context(view_model.movie)
    return await render_template(
        "movie.html",
        movie=view_model.movie,
        previous_count=view_model.previous_count,
        **image_context,
    )


@bp.route("/")
async def home():
    state = _current_state()
    data = await _services().movie_manager.home(state, legacy_session=_legacy_session())
    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
    )


@bp.route("/next_movie", methods=["POST"])
@csrf_required
@rate_limited("next_movie")
@with_timeout(_REQUEST_TIMEOUT)
async def next_movie():
    services = _services()
    state = _current_state()
    logger.info(
        "Requesting next movie for session_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    services.metrics_collector.track_movie_recommendation("next_movie")
    user_actions_total.labels(action_type="next_movie").inc()

    outcome = await services.movie_manager.next_movie(
        state,
        legacy_session=_legacy_session(),
    )

    if outcome is not None:
        return await _redirect_for_navigation_outcome(outcome)

    logger.warning("No more movies available. Correlation ID: %s", g.correlation_id)
    return "No more movies available. Please try again later.", 200


@bp.route("/previous_movie", methods=["POST"])
@csrf_required
@rate_limited("previous_movie")
@with_timeout(_REQUEST_TIMEOUT)
async def previous_movie():
    movie_manager = _services().movie_manager
    state = _current_state()
    logger.info(
        "Requesting previous movie for session_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )
    outcome = await movie_manager.previous_movie(state, legacy_session=_legacy_session())

    if outcome is None:
        tconst = movie_manager.get_current_movie_tconst(state)
        if tconst:
            return redirect(url_for("main.movie_detail", tconst=tconst))
        return redirect(url_for("main.home"))

    return await _redirect_for_navigation_outcome(outcome)


@bp.route("/filters")
async def set_filters():
    state = _current_state()
    current_filters = state.filters

    start_time = time.time()
    logger.info(
        "Starting to set filters for session_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    response = await _render_filters_page(current_filters)
    elapsed_time = time.time() - start_time
    logger.info(
        "Completed setting filters for session_id: %s in %.2f seconds. Correlation ID: %s",
        state.session_id,
        elapsed_time,
        g.correlation_id,
    )
    return response


@bp.route("/filtered_movie", methods=["POST"])
@csrf_required
@rate_limited("filtered_movie")
@with_timeout(_REQUEST_TIMEOUT)
async def filtered_movie_endpoint():
    movie_manager = _services().movie_manager
    state = _current_state()
    form_data = await request.form
    filters: FilterState = normalize_filters(form_data)
    validation_errors = validate_filters(filters)

    start_time = time.time()
    logger.info(
        "Starting filtering movies for session_id: %s. Correlation ID: %s",
        state.session_id,
        g.correlation_id,
    )

    wants_json = _wants_json_response()

    if validation_errors:
        logger.info(
            "Rejected invalid filters for session_id: %s. Correlation ID: %s. Errors: %s",
            state.session_id,
            g.correlation_id,
            validation_errors,
        )
        elapsed_time = time.time() - start_time
        logger.info(
            "Completed filtering movies for session_id: %s in %.2f seconds. Correlation ID: %s",
            state.session_id,
            elapsed_time,
            g.correlation_id,
        )
        if wants_json:
            return jsonify({"ok": False, "errors": validation_errors}), 400
        return await _render_filters_page(
            filters,
            validation_errors=validation_errors,
            form_notice="Fix the highlighted filters and try again.",
            genres_notice=(
                "No genres selected. Nextreel will use all genres."
                if not filters.get("genres_selected")
                else None
            ),
            status_code=400,
        )

    outcome = await movie_manager.apply_filters(
        state,
        filters,
        legacy_session=_legacy_session(),
    )
    elapsed_time = time.time() - start_time
    logger.info(
        "Completed filtering movies for session_id: %s in %.2f seconds. Correlation ID: %s",
        state.session_id,
        elapsed_time,
        g.correlation_id,
    )
    if outcome is not None:
        if wants_json:
            if outcome.tconst:
                return jsonify({
                    "ok": True,
                    "redirect": url_for("main.movie_detail", tconst=outcome.tconst),
                })
            return _no_matches_response()
        return await _redirect_for_navigation_outcome(outcome)
    if wants_json:
        return _no_matches_response()
    await flash("No movies matched your filters. Try broadening your criteria.", "warning")
    return redirect(url_for("main.set_filters"))


def _parse_watched_pagination(args) -> tuple[int, int, int]:
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


@bp.route("/watched")
async def watched_list_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()

    page, per_page, offset = _parse_watched_pagination(request.args)

    raw_rows, total_count = await asyncio.gather(
        services.movie_manager.watched_store.list_watched(
            user_id, limit=per_page, offset=offset
        ),
        services.movie_manager.watched_store.count(user_id),
    )

    view_model = _watched_list_presenter.build(
        raw_rows=raw_rows,
        total_count=total_count,
        page=page,
        per_page=per_page,
        now=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    return await render_template(
        "watched_list.html",
        movies=view_model.movies,
        stats=view_model.stats,
        total=view_model.total,
        pagination=view_model.pagination,
    )


@bp.route("/watched/add/<tconst>", methods=["POST"])
@csrf_required
@rate_limited("watched")
async def add_to_watched(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await _watched_mutation_service.add(
        user_id=user_id,
        tconst=tconst,
        watched_store=services.movie_manager.watched_store,
    )
    logger.info("User %s marked %s as watched", user_id, tconst)
    if _wants_json_response():
        return jsonify({
            "ok": True,
            "is_watched": True,
            "tconst": tconst,
        })

    return redirect(_safe_referrer(tconst), code=303)


@bp.route("/watched/remove/<tconst>", methods=["POST"])
@csrf_required
@rate_limited("watched")
async def remove_from_watched(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await _watched_mutation_service.remove(
        user_id=user_id,
        tconst=tconst,
        watched_store=services.movie_manager.watched_store,
    )
    logger.info("User %s removed %s from watched", user_id, tconst)
    if _wants_json_response():
        return jsonify({
            "ok": True,
            "is_watched": False,
            "tconst": tconst,
        })

    return redirect(_safe_referrer(tconst), code=303)


@bp.route("/auth/google")
async def auth_google():
    oauth_config = getattr(current_app, "oauth_config", {})
    if not oauth_config.get("google_enabled"):
        abort(404, "Google sign-in not configured")

    state_token = stdlib_secrets.token_urlsafe(32)
    session[SESSION_OAUTH_STATE_KEY] = state_token

    auth_url = _google_oauth_service.build_authorize_url(
        oauth_config=oauth_config,
        state_token=state_token,
    )
    return redirect(auth_url)


@bp.route("/auth/google/callback")
async def auth_google_callback():
    oauth_config = getattr(current_app, "oauth_config", {})
    if not oauth_config.get("google_enabled"):
        abort(404)

    expected_state = session.pop(SESSION_OAUTH_STATE_KEY, None)
    services = _services()
    outcome = await _google_oauth_service.complete_login(
        oauth_config=oauth_config,
        expected_state=expected_state,
        received_state=request.args.get("state", ""),
        code=request.args.get("code"),
        db_pool=services.movie_manager.db_pool,
    )
    if outcome.kind == "failure":
        if expected_state and not _hmac.compare_digest(expected_state, request.args.get("state", "")):
            logger.warning("OAuth state mismatch — possible CSRF attempt")
        return await _oauth_fail(outcome.error_message)
    if outcome.kind == "provider_conflict":
        await flash(outcome.error_message, "error")
        return redirect(url_for("main.login_page"))

    state = _current_state()
    user_id = outcome.user_id
    await current_app.navigation_state_store.set_user_id(state.session_id, user_id)
    state.user_id = user_id
    logger.info("User %s logged in via Google, session %s", user_id, state.session_id)
    return redirect(url_for("main.home"), code=303)


@bp.route("/auth/apple")
async def auth_apple():
    oauth_config = getattr(current_app, "oauth_config", {})
    if not oauth_config.get("apple_enabled"):
        abort(404, "Apple sign-in not configured")
    abort(501, "Apple sign-in coming soon")


@bp.route("/auth/apple/callback", methods=["POST"])
async def auth_apple_callback():
    abort(501, "Apple sign-in coming soon")
