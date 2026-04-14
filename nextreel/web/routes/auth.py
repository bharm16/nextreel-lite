"""Auth-related route handlers."""

from __future__ import annotations

import hmac as _hmac
import secrets as stdlib_secrets

from quart import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from infra.route_helpers import csrf_required, rate_limited
from nextreel.web.routes.shared import (
    _attach_user_to_current_session,
    _current_state,
    _current_user_id,
    _get_csrf_token,
    _google_oauth_service,
    _registration_service,
    _services,
    _watched_mutation_service,
    _current_year,
    bp,
    logger,
    user_avatar_info,
)
from session import user_preferences
from session.keys import SESSION_OAUTH_STATE_KEY
from session.user_auth import get_user_by_id


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
        "user_avatar_info": user_avatar_info,
    }


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

    state = await _attach_user_to_current_session(user_id)
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

    user_id = outcome.user_id
    state = await _attach_user_to_current_session(user_id)
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
        if expected_state and not _hmac.compare_digest(
            expected_state, request.args.get("state", "")
        ):
            logger.warning("OAuth state mismatch — possible CSRF attempt")
        await flash(outcome.error_message, "error")
        return redirect(url_for("main.login_page"))
    if outcome.kind == "provider_conflict":
        await flash(outcome.error_message, "error")
        return redirect(url_for("main.login_page"))

    user_id = outcome.user_id
    state = await _attach_user_to_current_session(user_id)
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


__all__ = [
    "auth_apple",
    "auth_apple_callback",
    "auth_google",
    "auth_google_callback",
    "inject_csrf_token",
    "login_page",
    "login_submit",
    "logout",
    "register_page",
    "register_submit",
]
