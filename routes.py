"""Application route handlers."""

from dataclasses import dataclass
import json
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

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
from infra.route_helpers import csrf_required, rate_limited, validate_csrf, with_timeout
from logging_config import get_logger
from movie_navigator import NavigationOutcome

if TYPE_CHECKING:
    from infra.metrics import MetricsCollector
    from movie_service import MovieManager

logger = get_logger(__name__)

bp = Blueprint("main", __name__)

_REQUEST_TIMEOUT = 30
_TCONST_RE = re.compile(r"^tt\d{1,10}$")


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


def _safe_referrer(fallback_tconst: str) -> str:
    """Return request.referrer only if it shares our origin; otherwise fall back."""
    referrer = request.referrer
    if referrer and referrer.startswith(request.host_url):
        return referrer
    return url_for("main.movie_detail", tconst=fallback_tconst)


def _redirect_for_navigation_outcome(outcome: NavigationOutcome):
    if outcome.state_conflict:
        if outcome.tconst:
            return redirect(
                url_for("main.movie_detail", tconst=outcome.tconst, state_conflict=1),
                code=303,
            )
        return redirect(url_for("main.home", state_conflict=1), code=303)
    if outcome.tconst:
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
        current_year=datetime.now(timezone.utc).year,
        validation_errors=validation_errors or {},
        form_notice=form_notice,
        genres_notice=genres_notice,
    )
    return response, status_code


# CSRF validation is canonical in infra.route_helpers.validate_csrf.
# Alias kept for any external callers.
_validate_csrf_from_form = validate_csrf


@bp.app_context_processor
def inject_csrf_token():
    state = getattr(g, "navigation_state", None)
    user_id = getattr(state, "user_id", None) if state else None
    oauth_config = getattr(current_app, "oauth_config", {})
    return {
        "csrf_token": _get_csrf_token,
        "current_user_id": user_id,
        "current_filters": (getattr(state, "filters", None) or {}),
        "current_year": datetime.now(timezone.utc).year,
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
    from session.user_auth import get_user_by_email, register_user, validate_registration

    form_data = await request.form
    email = form_data.get("email", "").strip()
    password = form_data.get("password", "")
    confirm_password = form_data.get("confirm_password", "")
    display_name = form_data.get("display_name", "").strip() or None

    errors = validate_registration(email, password, confirm_password)
    if errors:
        return await render_template("register.html", errors=errors), 400

    services = _services()
    existing = await get_user_by_email(services.movie_manager.db_pool, email)
    if existing:
        return (
            await render_template(
                "register.html",
                errors={"email": "An account with this email already exists."},
            ),
            400,
        )

    user_id = await register_user(services.movie_manager.db_pool, email, password, display_name)

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


@bp.route("/ready")
async def readiness_check():
    movie_manager = _services().movie_manager
    if not check_ops_auth():
        return {"error": "unauthorized"}, 401
    if not await check_rate_limit("ready"):
        return {"error": "rate limited"}, 429

    try:
        pool_metrics = await movie_manager.db_pool.get_metrics()
        if pool_metrics["circuit_breaker_state"] == "open":
            return {"status": "not_ready", "reason": "database_circuit_breaker_open"}, 503

        navigation_ready = await current_app.navigation_state_store.ready_check()
        candidates_fresh = await movie_manager.candidate_store.has_fresh_data()
        projection_ready = await movie_manager.projection_store.ready_check()
        status_code = 200 if navigation_ready and candidates_fresh and projection_ready else 503

        return {
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
        }, status_code
    except Exception as exc:
        logger.error("Readiness check failed: %s", exc)
        return {"status": "not_ready", "reason": "internal_error"}, 503


@bp.route("/movie/<tconst>")
@with_timeout(_REQUEST_TIMEOUT)
async def movie_detail(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")

    state = _current_state()
    user_id = _current_user_id()
    if user_id:
        g.is_watched = await _services().movie_manager.watched_store.is_watched(user_id, tconst)
    else:
        g.is_watched = False

    logger.debug(
        "Fetching movie details for tconst: %s, session_id: %s. Correlation ID: %s",
        tconst,
        state.session_id,
        g.correlation_id,
    )
    return await _services().movie_manager.render_movie_by_tconst(
        state,
        tconst,
        template_name="movie.html",
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
        return _redirect_for_navigation_outcome(outcome)

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

    return _redirect_for_navigation_outcome(outcome)


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
            return jsonify({
                "ok": False,
                "errors": {"form": "No movies matched your filters. Try broadening your criteria."},
            })
        return _redirect_for_navigation_outcome(outcome)
    if wants_json:
        return jsonify({
            "ok": False,
            "errors": {"form": "No movies matched your filters. Try broadening your criteria."},
        })
    await flash("No movies matched your filters. Try broadening your criteria.", "warning")
    return redirect(url_for("main.set_filters"))


@bp.route("/watched")
async def watched_list_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()

    raw_rows = await services.movie_manager.watched_store.list_all_watched(user_id)

    movies: list[dict] = []
    year_values: list[int] = []
    this_month_count = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_year = now.year
    current_month = now.month

    for row in raw_rows:
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
            continue
        title = payload.get("title") or row.get("primaryTitle") or "Untitled"
        slug = payload.get("slug") or row.get("slug")

        year_raw = payload.get("year") or row.get("startYear")
        try:
            year_int = int(str(year_raw)[:4]) if year_raw else None
        except (TypeError, ValueError):
            year_int = None
        if year_int:
            year_values.append(year_int)

        try:
            tmdb_rating = float(payload.get("rating") or 0)
        except (TypeError, ValueError):
            tmdb_rating = 0.0

        poster_url = payload.get("poster_url") or "/static/img/poster-placeholder.svg"

        watched_at = row.get("watched_at")
        watched_iso = watched_at.isoformat() if hasattr(watched_at, "isoformat") else str(watched_at or "")
        if hasattr(watched_at, "year") and watched_at.year == current_year and watched_at.month == current_month:
            this_month_count += 1

        movies.append({
            "tconst": tconst,
            "slug": slug,
            "title": title,
            "year": year_int,
            "poster_url": poster_url,
            "tmdb_rating": tmdb_rating,
            "watched_at": watched_iso,
        })

    total = len(movies)
    avg_year = int(round(sum(year_values) / len(year_values))) if year_values else None
    if year_values:
        decade_counts: dict[int, int] = {}
        for y in year_values:
            d = (y // 10) * 10
            decade_counts[d] = decade_counts.get(d, 0) + 1
        top_decade_year = max(decade_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        top_decade = "%ds" % top_decade_year
    else:
        top_decade = None

    stats = {
        "total": total,
        "this_month": this_month_count,
        "avg_year": avg_year,
        "top_decade": top_decade,
    }

    return await render_template(
        "watched_list.html",
        movies=movies,
        stats=stats,
        total=total,
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

    import secrets as stdlib_secrets
    from urllib.parse import urlencode

    state_token = stdlib_secrets.token_urlsafe(32)
    session["oauth_state"] = state_token

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
    import hmac as _hmac

    from session.user_auth import find_or_create_oauth_user, get_user_by_email

    oauth_config = getattr(current_app, "oauth_config", {})
    if not oauth_config.get("google_enabled"):
        abort(404)

    # Validate OAuth state to prevent login CSRF
    expected_state = session.pop("oauth_state", None)
    received_state = request.args.get("state", "")
    if not expected_state or not _hmac.compare_digest(expected_state, received_state):
        logger.warning("OAuth state mismatch — possible CSRF attempt")
        await flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("main.login_page"))

    code = request.args.get("code")
    if not code:
        await flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("main.login_page"))

    import httpx

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
            await flash("Google sign-in failed. Please try again.", "error")
            return redirect(url_for("main.login_page"))

        tokens = token_response.json()

        userinfo_response = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if userinfo_response.status_code != 200:
            await flash("Google sign-in failed. Please try again.", "error")
            return redirect(url_for("main.login_page"))

        userinfo = userinfo_response.json()

    email = userinfo.get("email")
    oauth_sub = userinfo.get("sub")
    display_name = userinfo.get("name")

    if not email or not oauth_sub:
        await flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("main.login_page"))

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
