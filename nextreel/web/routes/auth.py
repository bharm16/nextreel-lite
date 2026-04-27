"""Auth-related route handlers."""

from __future__ import annotations

import hmac as _hmac
import secrets as stdlib_secrets

from quart import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from infra.event_schema import (
    EVENT_LOGGED_OUT,
    EVENT_LOGIN_FAILED,
    EVENT_LOGIN_SUCCEEDED,
    EVENT_OAUTH_FAILED,
    EVENT_SIGNUP_COMPLETED,
    EVENT_SIGNUP_FAILED,
)
from infra.events import (
    anon_distinct_id,
    bind_authenticated_identity,
    track_event,
)
from infra.metrics import session_duration_seconds, user_actions_total, user_sessions_total
from infra.route_helpers import csrf_required, rate_limited
from infra.time_utils import utcnow
from nextreel.web.routes.shared import (
    _attach_user_to_current_session,
    _current_state,
    _current_user_id,
    _get_csrf_token,
    _google_oauth_service,
    _registration_service,
    _services,
    _current_year,
    bp,
    logger,
    user_avatar_info,
)
from session import user_preferences
from session.keys import SESSION_OAUTH_NEXT_KEY, SESSION_OAUTH_STATE_KEY
from session.user_auth import get_user_by_id


@bp.app_context_processor
def inject_csrf_token():
    state = getattr(g, "navigation_state", None)
    user_id = getattr(state, "user_id", None) if state else None
    oauth_config = getattr(current_app, "oauth_config", {})
    posthog_config = getattr(current_app, "posthog_config", None) or {}
    return {
        "csrf_token": _get_csrf_token,
        "current_user_id": user_id,
        "current_filters": (getattr(state, "filters", None) or {}),
        "current_year": _current_year(),
        "is_watched": getattr(g, "is_watched", False),
        "is_in_watchlist": getattr(g, "is_in_watchlist", False),
        "google_enabled": oauth_config.get("google_enabled", False),
        "user_avatar_info": user_avatar_info,
        "posthog_enabled": bool(posthog_config.get("enabled")),
        "posthog_project_key": posthog_config.get("project_key", ""),
        "posthog_api_host": posthog_config.get("api_host", "/ph"),
    }


@bp.app_context_processor
async def inject_current_user_auth_provider():
    """Surface the auth_provider with its 'email' default in Python, not Jinja.

    Templates were doing ``current_user.auth_provider or 'email'`` inline,
    which silently swallows missing-attribute bugs (e.g. if the user row
    schema changes). Resolving here keeps templates focused on rendering.
    """
    user, _theme = await _load_current_user_once()
    auth_provider = user.get("auth_provider") if user else None
    return {"current_user_auth_provider": auth_provider or "email"}


async def _load_current_user_once():
    """Load the current user row and theme preference once per request.

    Cached on ``g`` so repeated template renders (context processors fire on
    every render) don't re-hit the DB. Returns ``(user, theme)``.
    """
    if hasattr(g, "_account_context_cache"):
        return g._account_context_cache
    user_id = _current_user_id()
    user = None
    theme = None
    if user_id:
        try:
            db_pool = _services().movie_manager.db_pool
        except Exception:
            db_pool = None
        if db_pool is not None:
            try:
                user = await get_user_by_id(db_pool, user_id)
            except Exception:
                user = None
            try:
                theme = await user_preferences.get_theme_preference(db_pool, user_id)
            except Exception:
                theme = None
    g._account_context_cache = (user, theme)
    return user, theme


@bp.app_context_processor
async def inject_account_context():
    user, theme = await _load_current_user_once()
    return {"current_user": user, "server_theme": theme}


def _safe_next_path(value: str | None) -> str | None:
    """Return value only if it's a safe relative path (no open-redirect).

    Callers must rely on Jinja autoescape when rendering this value — never
    mark it ``|safe`` in templates. The helper enforces shape only, not
    HTML/URL-context sanitization.
    """
    if not value:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        return None
    if not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return None
    return value


@bp.route("/login")
async def login_page():
    if _current_user_id():
        return redirect(url_for("main.home"))
    next_path = _safe_next_path(request.args.get("next"))
    return await render_template("login.html", errors={}, next_path=next_path)


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
    next_path = _safe_next_path(form_data.get("next"))

    user_actions_total.labels(action_type="login_attempt").inc()

    services = _services()
    try:
        user_id = await authenticate_user(services.movie_manager.db_pool, email, password)
    except EmailPasswordAuthUnavailableError:
        logger.warning("Email/password login unavailable: bcrypt dependency missing")
        user_actions_total.labels(action_type="login_failure_unavailable").inc()
        # NOTE: EVENT_LOGIN_FAILED is for funnel analytics (signup → first
        # login conversion), NOT brute-force / account-takeover monitoring.
        # Use the ``user_actions_total{action_type="login_failure_*"}``
        # Prometheus counter for that — it has full IP cardinality and
        # isn't redacted into a hashed anon ID.
        track_event(
            anon_distinct_id(_current_state().session_id),
            EVENT_LOGIN_FAILED,
            {"reason": "unavailable"},
        )
        return (
            await render_template(
                "login.html",
                errors={"form": EMAIL_PASSWORD_AUTH_UNAVAILABLE_MESSAGE},
                next_path=next_path,
            ),
            503,
        )

    if not user_id:
        user_actions_total.labels(action_type="login_failure_invalid").inc()
        track_event(
            anon_distinct_id(_current_state().session_id),
            EVENT_LOGIN_FAILED,
            {"reason": "invalid_credentials"},
        )
        return (
            await render_template(
                "login.html",
                errors={"form": "Invalid email or password."},
                next_path=next_path,
            ),
            401,
        )

    state = await _attach_user_to_current_session(user_id)
    user_actions_total.labels(action_type="login_success").inc()
    user_sessions_total.inc()
    bind_authenticated_identity(
        anon_id=anon_distinct_id(state.session_id),
        user_id=user_id,
        event=EVENT_LOGIN_SUCCEEDED,
        user_properties={"auth_provider": "email"},
    )
    logger.info("User %s logged in, session %s", user_id, state.session_id)
    return redirect(next_path or url_for("main.home"), code=303)


@bp.route("/register")
async def register_page():
    if _current_user_id():
        return redirect(url_for("main.home"))
    next_path = _safe_next_path(request.args.get("next"))
    return await render_template("register.html", errors={}, next_path=next_path)


@bp.route("/register", methods=["POST"])
@csrf_required
@rate_limited("register")
async def register_submit():
    form_data = await request.form
    email = form_data.get("email", "").strip()
    password = form_data.get("password", "")
    confirm_password = form_data.get("confirm_password", "")
    display_name = form_data.get("display_name", "").strip() or None
    next_path = _safe_next_path(form_data.get("next"))

    user_actions_total.labels(action_type="register_attempt").inc()

    services = _services()
    outcome = await _registration_service.register_email_user(
        email=email,
        password=password,
        confirm_password=confirm_password,
        display_name=display_name,
        db_pool=services.movie_manager.db_pool,
    )
    if outcome.kind != "success":
        # The Prometheus counter labels (register_failure_unavailable /
        # _duplicate / _validation) and the EVENT_SIGNUP_FAILED ``reason``
        # property are kept aligned — they always agree, even though they
        # serve different consumers (Grafana vs PostHog funnel).
        if outcome.kind == "service_unavailable":
            logger.warning("Email/password registration unavailable: bcrypt dependency missing")
            user_actions_total.labels(action_type="register_failure_unavailable").inc()
            failure_reason = "unavailable"
        elif outcome.kind == "duplicate_email":
            user_actions_total.labels(action_type="register_failure_duplicate").inc()
            failure_reason = "duplicate"
        else:
            user_actions_total.labels(action_type="register_failure_validation").inc()
            failure_reason = "validation"
        track_event(
            anon_distinct_id(_current_state().session_id),
            EVENT_SIGNUP_FAILED,
            {"reason": failure_reason},
        )
        status_code = 503 if outcome.kind == "service_unavailable" else 400
        return (
            await render_template(
                "register.html", errors=outcome.errors, next_path=next_path
            ),
            status_code,
        )

    user_id = outcome.user_id
    state = await _attach_user_to_current_session(user_id)
    user_actions_total.labels(action_type="register_success").inc()
    user_sessions_total.inc()
    bind_authenticated_identity(
        anon_id=anon_distinct_id(state.session_id),
        user_id=user_id,
        event=EVENT_SIGNUP_COMPLETED,
        user_properties={"auth_provider": "email", "signup_at": utcnow().isoformat()},
        event_properties={"auth_provider": "email"},
    )
    logger.info("User %s registered, session %s", user_id, state.session_id)
    return redirect(next_path or url_for("main.home"), code=303)


@bp.route("/logout", methods=["POST"])
@csrf_required
async def logout():
    state = _current_state()
    if state.user_id:
        logged_out_user_id = state.user_id
        await current_app.navigation_state_store.set_user_id(state.session_id, None)
        state.user_id = None
        user_actions_total.labels(action_type="logout").inc()
        # Bound floor at 0: if a clock skew or stale state.created_at ever
        # produces a negative delta, drop it rather than poison the histogram.
        duration = (utcnow() - state.created_at).total_seconds()
        if duration >= 0:
            session_duration_seconds.observe(duration)
        track_event(
            logged_out_user_id,
            EVENT_LOGGED_OUT,
            {"session_duration_seconds": max(duration, 0.0)},
        )
        logger.info("User logged out, session %s", state.session_id)
    response = redirect(url_for("main.home"), code=303)
    return response


@bp.route("/auth/google")
async def auth_google():
    oauth_config = getattr(current_app, "oauth_config", {})
    if not oauth_config.get("google_enabled"):
        abort(404, "Google sign-in not configured")

    state_token = stdlib_secrets.token_urlsafe(32)
    session[SESSION_OAUTH_STATE_KEY] = state_token

    next_path = _safe_next_path(request.args.get("next"))
    if next_path:
        session[SESSION_OAUTH_NEXT_KEY] = next_path
    else:
        session.pop(SESSION_OAUTH_NEXT_KEY, None)

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
    next_path = _safe_next_path(session.pop(SESSION_OAUTH_NEXT_KEY, None))
    user_actions_total.labels(action_type="oauth_callback_attempt").inc()
    services = _services()
    outcome = await _google_oauth_service.complete_login(
        oauth_config=oauth_config,
        expected_state=expected_state,
        received_state=request.args.get("state", ""),
        code=request.args.get("code"),
        db_pool=services.movie_manager.db_pool,
    )
    if outcome.kind == "failure":
        state_mismatch = bool(
            expected_state
            and not _hmac.compare_digest(expected_state, request.args.get("state", ""))
        )
        if state_mismatch:
            logger.warning("OAuth state mismatch — possible CSRF attempt")
            user_actions_total.labels(
                action_type="oauth_callback_failure_state_mismatch"
            ).inc()
            track_event(
                anon_distinct_id(_current_state().session_id),
                EVENT_OAUTH_FAILED,
                {"provider": "google", "reason": "state_mismatch"},
            )
        else:
            user_actions_total.labels(action_type="oauth_callback_failure").inc()
            track_event(
                anon_distinct_id(_current_state().session_id),
                EVENT_OAUTH_FAILED,
                {"provider": "google", "reason": "other"},
            )
        await flash(outcome.error_message, "error")
        return redirect(
            url_for("main.login_page", next=next_path) if next_path else url_for("main.login_page")
        )
    if outcome.kind == "provider_conflict":
        user_actions_total.labels(
            action_type="oauth_callback_failure_provider_conflict"
        ).inc()
        track_event(
            anon_distinct_id(_current_state().session_id),
            EVENT_OAUTH_FAILED,
            {"provider": "google", "reason": "provider_conflict"},
        )
        await flash(outcome.error_message, "error")
        return redirect(
            url_for("main.login_page", next=next_path) if next_path else url_for("main.login_page")
        )

    user_id = outcome.user_id
    state = await _attach_user_to_current_session(user_id)
    user_actions_total.labels(action_type="oauth_callback_success").inc()
    user_sessions_total.inc()
    bind_authenticated_identity(
        anon_id=anon_distinct_id(state.session_id),
        user_id=user_id,
        event=EVENT_LOGIN_SUCCEEDED,
        user_properties={"auth_provider": "google"},
    )
    logger.info("User %s logged in via Google, session %s", user_id, state.session_id)
    return redirect(next_path or url_for("main.home"), code=303)


__all__ = [
    "auth_google",
    "auth_google_callback",
    "inject_csrf_token",
    "login_page",
    "login_submit",
    "logout",
    "register_page",
    "register_submit",
]
