"""Account settings routes — Profile, Security, Preferences, Data, Danger zone."""

from __future__ import annotations

import csv
import io
import json as _json
from uuid import uuid4

from quart import (
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from infra.route_helpers import csrf_required, rate_limited
from infra.time_utils import utcnow
from logging_config import get_logger
from movies.filter_parser import extract_movie_filter_criteria
from nextreel.web.routes.shared import (
    _current_user_id,
    _services,
    bp,
)
from session import user_preferences
from session.revocation import revoke_user_sessions
from session.user_auth import (
    MIN_PASSWORD_LENGTH,
    get_user_by_id,
    hash_password_async,
    verify_password_async,
)

logger = get_logger(__name__)

_VALID_TABS = ("profile", "security", "preferences", "data", "danger")
MAX_DISPLAY_NAME_LENGTH = 100
MAX_LETTERBOXD_CSV_BYTES = 5 * 1024 * 1024  # 5 MB


def _db_pool():
    return _services().movie_manager.db_pool


def _require_user() -> str:
    user_id = _current_user_id()
    if not user_id:
        abort(redirect(url_for("main.login_page")))
    return user_id


def _redis_client():
    return current_app.config.get("SESSION_REDIS")


def _current_sid() -> str | None:
    if hasattr(session, "sid"):
        return session.sid
    return session.get("_id")


@bp.route("/account")
async def account_view():
    if not _current_user_id():
        return redirect(url_for("main.login_page", next="/account?tab=profile"))

    tab = request.args.get("tab", "profile")
    if tab not in _VALID_TABS:
        return redirect(url_for("main.account_view") + "?tab=profile")

    db_pool = _db_pool()
    user_id = _current_user_id()
    user = await get_user_by_id(db_pool, user_id)
    if not user:
        session.clear()
        return redirect(url_for("main.login_page"))

    exclude_watched_default = await user_preferences.get_exclude_watched_default(
        db_pool, user_id
    )
    theme_preference = await user_preferences.get_theme_preference(db_pool, user_id)
    default_filters = await user_preferences.get_default_filters(db_pool, user_id)

    return await render_template(
        f"account/{tab}.html",
        active_tab=tab,
        user=user,
        server_theme=theme_preference,
        exclude_watched_default=exclude_watched_default,
        default_filters=default_filters,
        page_title=tab.title(),
    )


# ── Profile ────────────────────────────────────────────────────────


@bp.route("/account/profile", methods=["POST"])
@csrf_required
async def account_profile_save():
    user_id = _require_user()
    form = await request.form
    raw = (form.get("display_name") or "").strip()
    if len(raw) > MAX_DISPLAY_NAME_LENGTH:
        abort(400, description="Display name too long")
    display_name = raw or None

    await _db_pool().execute(
        "UPDATE users SET display_name = %s, updated_at = %s WHERE user_id = %s",
        [display_name, utcnow(), user_id],
        fetch="none",
    )
    logger.info("Account action: %s user=%s", "profile_save", user_id)
    return redirect(url_for("main.account_view") + "?tab=profile")


# ── Security ───────────────────────────────────────────────────────


@bp.route("/account/password", methods=["POST"])
@csrf_required
@rate_limited("account_password")
async def account_password_change():
    user_id = _require_user()
    form = await request.form
    current = form.get("current_password", "")
    new = form.get("new_password", "")
    confirm = form.get("confirm_password", "")

    errors: dict[str, str] = {}
    if len(new) < MIN_PASSWORD_LENGTH:
        errors["new_password"] = (
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if new != confirm:
        errors["confirm_password"] = "Passwords do not match."

    db_pool = _db_pool()
    row = await db_pool.execute(
        "SELECT password_hash FROM users "
        "WHERE user_id = %s AND auth_provider = 'email'",
        [user_id],
        fetch="one",
    )
    if not row or not row.get("password_hash"):
        abort(400, description="Password change is only available for email accounts.")

    if not await verify_password_async(current, row["password_hash"]):
        errors["current_password"] = "Current password is incorrect."

    if errors:
        user = await get_user_by_id(db_pool, user_id)
        theme = await user_preferences.get_theme_preference(db_pool, user_id)
        return (
            await render_template(
                "account/security.html",
                active_tab="security",
                user=user,
                server_theme=theme,
                errors=errors,
            ),
            400,
        )

    new_hash = await hash_password_async(new)
    await db_pool.execute(
        "UPDATE users SET password_hash = %s, updated_at = %s WHERE user_id = %s",
        [new_hash, utcnow(), user_id],
        fetch="none",
    )

    redis_client = _redis_client()
    if redis_client is not None:
        await revoke_user_sessions(
            redis_client, user_id, except_session_id=_current_sid()
        )

    logger.info("Account action: %s user=%s", "password_change", user_id)
    return redirect(url_for("main.account_view") + "?tab=security")


@bp.route("/account/sessions/revoke", methods=["POST"])
@csrf_required
async def account_sessions_revoke():
    user_id = _require_user()
    redis_client = _redis_client()
    if redis_client is None:
        abort(503, description="Session store unavailable.")
    revoked = await revoke_user_sessions(
        redis_client, user_id, except_session_id=_current_sid()
    )
    logger.info(
        "Account action: %s user=%s revoked=%d", "sessions_revoke", user_id, revoked
    )
    return redirect(url_for("main.account_view") + "?tab=security")


# ── Preferences ────────────────────────────────────────────────────


@bp.route("/account/preferences", methods=["POST"])
@csrf_required
async def account_preferences_save():
    user_id = _require_user()
    form = await request.form
    exclude = form.get("exclude_watched_default") == "on"
    theme_raw = form.get("theme_preference", "system")
    theme = theme_raw if theme_raw in ("light", "dark") else None

    db_pool = _db_pool()
    await user_preferences.set_exclude_watched_default(db_pool, user_id, exclude)
    await user_preferences.set_theme_preference(db_pool, user_id, theme)
    logger.info("Account action: %s user=%s", "preferences_save", user_id)
    return redirect(url_for("main.account_view") + "?tab=preferences")


@bp.route("/account/preferences/filters/save", methods=["POST"])
@csrf_required
async def account_filters_save():
    user_id = _require_user()
    form = await request.form
    filters = extract_movie_filter_criteria(form)
    await user_preferences.set_default_filters(_db_pool(), user_id, filters)
    logger.info("Account action: %s user=%s", "filters_save_default", user_id)
    return redirect(url_for("main.account_view") + "?tab=preferences")


@bp.route("/account/preferences/filters/clear", methods=["POST"])
@csrf_required
async def account_filters_clear():
    user_id = _require_user()
    await user_preferences.clear_default_filters(_db_pool(), user_id)
    logger.info("Account action: %s user=%s", "filters_clear_default", user_id)
    return redirect(url_for("main.account_view") + "?tab=preferences")


# ── Data tab: Letterboxd import ────────────────────────────────────


@bp.route("/account/import/letterboxd", methods=["POST"])
@csrf_required
@rate_limited("account_letterboxd_import")
async def account_letterboxd_upload():
    user_id = _require_user()
    files = await request.files
    upload = files.get("csv")
    if upload is None:
        abort(400, description="No file provided.")

    data = upload.read()
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not data:
        abort(400, description="File is empty.")
    if len(data) > MAX_LETTERBOXD_CSV_BYTES:
        abort(413, description="File too large (max 5 MB).")

    import_id = uuid4().hex
    now = utcnow()
    db_pool = _db_pool()
    await db_pool.execute(
        """
        INSERT INTO letterboxd_imports
          (import_id, user_id, status, processed, matched, skipped, failed,
           created_at, updated_at)
        VALUES (%s, %s, 'pending', 0, 0, 0, 0, %s, %s)
        """,
        [import_id, user_id, now, now],
        fetch="none",
    )

    redis_client = _redis_client()
    if redis_client is None:
        abort(503, description="Storage unavailable.")
    await redis_client.set(
        f"letterboxd:import:{import_id}:csv",
        data,
        ex=60 * 60 * 24,
    )

    enqueue = getattr(current_app, "enqueue_runtime_job", None)
    if enqueue is None:
        abort(503, description="Job queue unavailable.")
    result = await enqueue("import_letterboxd", import_id)
    if result is None:
        logger.warning(
            "Letterboxd import enqueued with no job id user=%s import_id=%s",
            user_id,
            import_id,
        )

    logger.info(
        "Account action: %s user=%s import_id=%s",
        "letterboxd_upload",
        user_id,
        import_id,
    )
    return redirect(url_for("main.account_import_progress", import_id=import_id))


@bp.route("/account/import/<import_id>")
async def account_import_progress(import_id: str):
    user_id = _require_user()
    db_pool = _db_pool()
    row = await db_pool.execute(
        """
        SELECT import_id, status, total_rows, processed, matched, skipped,
               failed, error_message, created_at, completed_at
        FROM letterboxd_imports
        WHERE import_id = %s AND user_id = %s
        """,
        [import_id, user_id],
        fetch="one",
    )
    if not row:
        abort(404)
    user = await get_user_by_id(db_pool, user_id)
    theme = await user_preferences.get_theme_preference(db_pool, user_id)
    return await render_template(
        "account/import_progress.html",
        active_tab="data",
        import_row=row,
        user=user,
        server_theme=theme,
    )


@bp.route("/account/import/<import_id>/status")
async def account_import_status(import_id: str):
    user_id = _require_user()
    row = await _db_pool().execute(
        """
        SELECT status, total_rows, processed, matched, skipped, failed, error_message
        FROM letterboxd_imports
        WHERE import_id = %s AND user_id = %s
        """,
        [import_id, user_id],
        fetch="one",
    )
    if not row:
        abort(404)
    return jsonify(dict(row))


# ── Data tab: Exports ──────────────────────────────────────────────


@bp.route("/account/export/watched.csv")
@rate_limited("account_export")
async def account_export_watched_csv():
    user_id = _require_user()
    rows = (
        await _db_pool().execute(
            """
            SELECT w.tconst, w.watched_at,
                   COALESCE(c.primaryTitle, '') AS title,
                   COALESCE(c.startYear, '')    AS year
            FROM user_watched_movies w
            LEFT JOIN movie_candidates c ON c.tconst = w.tconst
            WHERE w.user_id = %s
            ORDER BY w.watched_at DESC
            """,
            [user_id],
            fetch="all",
        )
        or []
    )

    async def stream():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Date", "Name", "Year", "Letterboxd URI"])
        yield buf.getvalue()
        for r in rows:
            buf.seek(0)
            buf.truncate(0)
            watched_at = r.get("watched_at")
            date = watched_at.strftime("%Y-%m-%d") if watched_at else ""
            writer.writerow([date, r.get("title", ""), r.get("year", ""), ""])
            yield buf.getvalue()

    filename = f"nextreel-watched-{utcnow().strftime('%Y-%m-%d')}.csv"
    return Response(
        stream(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/account/export/watched.json")
@rate_limited("account_export")
async def account_export_watched_json():
    user_id = _require_user()
    rows = (
        await _db_pool().execute(
            """
            SELECT w.tconst, w.watched_at,
                   c.primaryTitle AS title,
                   c.startYear    AS year
            FROM user_watched_movies w
            LEFT JOIN movie_candidates c ON c.tconst = w.tconst
            WHERE w.user_id = %s
            ORDER BY w.watched_at DESC
            """,
            [user_id],
            fetch="all",
        )
        or []
    )

    async def stream():
        yield "["
        first = True
        for r in rows:
            if not first:
                yield ","
            first = False
            watched_at = r.get("watched_at")
            yield _json.dumps(
                {
                    "tconst": r["tconst"],
                    "title": r.get("title"),
                    "year": r.get("year"),
                    "watched_at": watched_at.isoformat() if watched_at else None,
                }
            )
        yield "]"

    filename = f"nextreel-watched-{utcnow().strftime('%Y-%m-%d')}.json"
    return Response(
        stream(),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Data tab: Clear watched ────────────────────────────────────────


@bp.route("/account/watched/clear", methods=["POST"])
@csrf_required
async def account_watched_clear():
    user_id = _require_user()
    await _db_pool().execute(
        "DELETE FROM user_watched_movies WHERE user_id = %s",
        [user_id],
        fetch="none",
    )
    logger.info("Account action: %s user=%s", "watched_clear", user_id)
    return redirect(url_for("main.account_view") + "?tab=data")


# ── Danger: delete account ─────────────────────────────────────────


@bp.route("/account/delete", methods=["POST"])
@csrf_required
@rate_limited("account_delete")
async def account_delete():
    user_id = _require_user()
    form = await request.form
    typed = (form.get("confirm_email") or "").strip().lower()

    db_pool = _db_pool()
    user = await get_user_by_id(db_pool, user_id)
    if not user:
        abort(400)
    if typed != user["email"].strip().lower():
        abort(400, description="Typed email does not match your account.")

    # Ordered cascade
    await db_pool.execute(
        "DELETE FROM user_watched_movies WHERE user_id = %s",
        [user_id],
        fetch="none",
    )
    await db_pool.execute(
        "DELETE FROM user_navigation_state WHERE user_id = %s",
        [user_id],
        fetch="none",
    )
    await db_pool.execute(
        "DELETE FROM letterboxd_imports WHERE user_id = %s",
        [user_id],
        fetch="none",
    )
    await db_pool.execute(
        "DELETE FROM users WHERE user_id = %s",
        [user_id],
        fetch="none",
    )

    redis_client = _redis_client()
    if redis_client is not None:
        await revoke_user_sessions(redis_client, user_id, except_session_id=None)

    session.clear()
    logger.info("Account action: %s user=%s", "account_delete", user_id)
    return redirect(url_for("main.home"))
