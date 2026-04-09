"""Application route handlers."""

import asyncio
import hmac as _hmac
import json
import re
import secrets as stdlib_secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
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
from infra.time_utils import current_year as _current_year, utcnow as _utcnow
from logging_config import get_logger
from movie_navigator import NavigationOutcome
from session.keys import SESSION_OAUTH_STATE_KEY

# NOTE: session.user_auth imports bcrypt eagerly. Keep this as a lazy
# in-function import in each auth route so test suites without bcrypt
# installed can still collect tests that import routes.py for blueprint
# registration (test_app.py, test_movie_navigator_extended.py, etc.).

if TYPE_CHECKING:
    from infra.metrics import MetricsCollector
    from movie_service import MovieManager

logger = get_logger(__name__)

bp = Blueprint("main", __name__)

_REQUEST_TIMEOUT = 30
_TCONST_RE = re.compile(r"^tt\d{1,10}$")

# /ready response cache: collapses bursts of k8s/LB probes onto a single
# set of DB round-trips. 5s stays responsive to real state changes but
# absorbs typical probe fan-out (k8s + LB + Prometheus within one window).
_READY_CACHE_TTL_SECONDS = 5.0
_ready_cache_entry: tuple[float, tuple[dict, int]] | None = None
_ready_cache_lock = asyncio.Lock()


def _no_matches_response():
    """JSON 'no movies matched' response shared by /filtered_movie branches."""
    return jsonify({
        "ok": False,
        "errors": {"form": "No movies matched your filters. Try broadening your criteria."},
    })


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
    from session.user_auth import authenticate_user

    form_data = await request.form
    email = form_data.get("email", "").strip()
    password = form_data.get("password", "")

    services = _services()
    user_id = await authenticate_user(services.movie_manager.db_pool, email, password)

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
    from session.user_auth import (
        DuplicateUserError,
        get_user_by_email,
        hash_password_async,
        register_user,
        validate_registration,
    )

    form_data = await request.form
    email = form_data.get("email", "").strip()
    password = form_data.get("password", "")
    confirm_password = form_data.get("confirm_password", "")
    display_name = form_data.get("display_name", "").strip() or None

    errors = validate_registration(email, password, confirm_password)
    if errors:
        return await render_template("register.html", errors=errors), 400

    services = _services()
    db_pool = services.movie_manager.db_pool

    # Run bcrypt hashing concurrently with the duplicate-email lookup.
    # On the duplicate-email branch we cancel the hash awaitable; note that
    # cancelling an asyncio.to_thread future does NOT stop the underlying
    # bcrypt work — the worker thread still runs to completion. That is
    # acceptable wasted CPU, not a correctness change. The wasted-CPU blast
    # radius is bounded by @rate_limited("register") above.
    hash_task = asyncio.create_task(hash_password_async(password))
    try:
        existing = await get_user_by_email(db_pool, email)
        if existing:
            hash_task.cancel()
            await asyncio.gather(hash_task, return_exceptions=True)
            return (
                await render_template(
                    "register.html",
                    errors={"email": "An account with this email already exists."},
                ),
                400,
            )

        password_hash = await hash_task
    except BaseException:
        if not hash_task.done():
            hash_task.cancel()
            await asyncio.gather(hash_task, return_exceptions=True)
        raise

    try:
        user_id = await register_user(
            db_pool,
            email,
            password,
            display_name,
            precomputed_hash=password_hash,
        )
    except DuplicateUserError:
        # TOCTOU race: another request registered the same email between
        # our duplicate-email check and INSERT. The UNIQUE constraint on
        # users.email caught it. Render the same error as the pre-check.
        return (
            await render_template(
                "register.html",
                errors={"email": "An account with this email already exists."},
            ),
            400,
        )

    state = _current_state()
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

    # Run the watched-status lookup concurrently with the projection payload
    # fetch. Each branch independently acquires a pooled DB connection via
    # its store method — no shared connection is reused across gathered legs.
    async def _watched_lookup() -> bool:
        if not user_id:
            return False
        return await movie_manager.watched_store.is_watched(user_id, tconst)

    async def _payload_lookup():
        return await movie_manager.projection_store.fetch_renderable_payload(tconst)

    is_watched_result, movie_data = await asyncio.gather(
        _watched_lookup(),
        _payload_lookup(),
    )
    g.is_watched = bool(is_watched_result)

    if not movie_data:
        logger.info("No data found for movie with tconst: %s", tconst)
        return "Movie not found", 404

    previous_count = movie_manager.prev_stack_length(state)
    return await render_template(
        "movie.html",
        movie=movie_data,
        previous_count=previous_count,
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

    wants_json = "application/json" in request.headers.get("Accept", "")

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


def _normalize_watched_row(row, now) -> tuple[dict | None, int | None, bool]:
    """Return (movie_dict, year_int, is_this_month) or (None, None, False) to skip."""
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}

    tconst = row.get("tconst")
    if not tconst:
        return None, None, False
    title = payload.get("title") or row.get("primaryTitle") or "Untitled"
    slug = payload.get("slug") or row.get("slug")

    year_raw = payload.get("year") or row.get("startYear")
    try:
        year_int = int(str(year_raw)[:4]) if year_raw else None
    except (TypeError, ValueError):
        year_int = None

    try:
        tmdb_rating = float(payload.get("rating") or 0)
    except (TypeError, ValueError):
        tmdb_rating = 0.0

    poster_url = payload.get("poster_url") or "/static/img/poster-placeholder.svg"

    watched_at = row.get("watched_at")
    watched_iso = watched_at.isoformat() if hasattr(watched_at, "isoformat") else str(watched_at or "")
    is_this_month = (
        hasattr(watched_at, "year")
        and watched_at.year == now.year
        and watched_at.month == now.month
    )

    movie = {
        "tconst": tconst,
        "slug": slug,
        "title": title,
        "year": year_int,
        "poster_url": poster_url,
        "tmdb_rating": tmdb_rating,
        "watched_at": watched_iso,
    }
    return movie, year_int, is_this_month


def _build_watched_stats(total: int, this_month_count: int, year_values: list[int]) -> dict:
    avg_year = int(round(sum(year_values) / len(year_values))) if year_values else None
    if year_values:
        decade_counts: dict[int, int] = {}
        for y in year_values:
            d = (y // 10) * 10
            decade_counts[d] = decade_counts.get(d, 0) + 1
        # Tie-break: prefer the more recent decade when counts are equal.
        top_decade_year = max(decade_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        top_decade = "%ds" % top_decade_year
    else:
        top_decade = None
    return {
        "total": total,
        "this_month": this_month_count,
        "avg_year": avg_year,
        "top_decade": top_decade,
    }


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

    movies: list[dict] = []
    year_values: list[int] = []
    this_month_count = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for row in raw_rows:
        movie, year_int, is_this_month = _normalize_watched_row(row, now)
        if movie is None:
            continue
        if year_int:
            year_values.append(year_int)
        if is_this_month:
            this_month_count += 1
        movies.append(movie)

    total = total_count
    stats = _build_watched_stats(total, this_month_count, year_values)

    total_pages = max(1, (total_count + per_page - 1) // per_page)
    pagination = {
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }

    return await render_template(
        "watched_list.html",
        movies=movies,
        stats=stats,
        total=total,
        pagination=pagination,
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
    await services.movie_manager.watched_store.add(user_id, tconst)
    logger.info("User %s marked %s as watched", user_id, tconst)

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
    await services.movie_manager.watched_store.remove(user_id, tconst)
    logger.info("User %s removed %s from watched", user_id, tconst)

    return redirect(_safe_referrer(tconst), code=303)


@bp.route("/auth/google")
async def auth_google():
    oauth_config = getattr(current_app, "oauth_config", {})
    if not oauth_config.get("google_enabled"):
        abort(404, "Google sign-in not configured")

    from urllib.parse import urlencode

    state_token = stdlib_secrets.token_urlsafe(32)
    session[SESSION_OAUTH_STATE_KEY] = state_token

    redirect_uri = "%s/auth/google/callback" % oauth_config["redirect_base"]
    params = urlencode({
        "client_id": oauth_config["google_client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state_token,
    })
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?%s" % params
    return redirect(auth_url)


@bp.route("/auth/google/callback")
async def auth_google_callback():
    from session.user_auth import find_or_create_oauth_user, get_user_by_email

    oauth_config = getattr(current_app, "oauth_config", {})
    if not oauth_config.get("google_enabled"):
        abort(404)

    # Validate OAuth state to prevent login CSRF
    expected_state = session.pop(SESSION_OAUTH_STATE_KEY, None)
    received_state = request.args.get("state", "")
    if not expected_state or not _hmac.compare_digest(expected_state, received_state):
        logger.warning("OAuth state mismatch — possible CSRF attempt")
        return await _oauth_fail("Google sign-in failed. Please try again.")

    code = request.args.get("code")
    if not code:
        return await _oauth_fail("Google sign-in failed. Please try again.")

    redirect_uri = "%s/auth/google/callback" % oauth_config["redirect_base"]

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": oauth_config["google_client_id"],
                "client_secret": oauth_config["google_client_secret"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != 200:
            return await _oauth_fail("Google sign-in failed. Please try again.")

        tokens = token_response.json()

        userinfo_response = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if userinfo_response.status_code != 200:
            return await _oauth_fail("Google sign-in failed. Please try again.")

        userinfo = userinfo_response.json()

    email = userinfo.get("email")
    oauth_sub = userinfo.get("sub")
    display_name = userinfo.get("name")

    if not email or not oauth_sub:
        return await _oauth_fail("Google sign-in failed. Please try again.")

    services = _services()
    db_pool = services.movie_manager.db_pool

    existing = await get_user_by_email(db_pool, email)
    if existing and existing["auth_provider"] != "google":
        provider = existing["auth_provider"]
        await flash(
            "An account with this email already exists. Please log in with %s." % provider,
            "error",
        )
        return redirect(url_for("main.login_page"))

    user_id = await find_or_create_oauth_user(
        db_pool,
        provider="google",
        oauth_sub=oauth_sub,
        email=email,
        display_name=display_name,
    )

    state = _current_state()
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
