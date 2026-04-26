"""Feature-namespaced route modules built on the shared blueprint."""

from nextreel.web.routes.account import (
    account_delete,
    account_export_watched_csv,
    account_export_watched_json,
    account_filters_clear,
    account_filters_save,
    account_import_progress,
    account_import_status,
    account_letterboxd_upload,
    account_password_change,
    account_preferences_save,
    account_profile_save,
    account_sessions_revoke,
    account_view,
    account_watched_clear,
)
from nextreel.web.routes.auth import (
    auth_google,
    auth_google_callback,
    inject_csrf_token,
    login_page,
    login_submit,
    logout,
    register_page,
    register_submit,
)
from nextreel.web.routes.movies import home, movie_detail
from nextreel.web.routes.navigation import (
    filtered_movie_endpoint,
    next_movie,
    previous_movie,
)
from nextreel.web.routes.ops import health_check, metrics, readiness_check
from nextreel.web.routes.search import projection_state, search_titles
from nextreel.web.routes.watched import (
    add_to_watched,
    remove_from_watched,
    watched_list_page,
)
from nextreel.web.routes.watchlist import (
    add_to_watchlist,
    remove_from_watchlist,
    watchlist_page,
)
from nextreel.web.routes.shared import bp, init_routes

__all__ = [
    "account_delete",
    "account_export_watched_csv",
    "account_export_watched_json",
    "account_filters_clear",
    "account_filters_save",
    "account_import_progress",
    "account_import_status",
    "account_letterboxd_upload",
    "account_password_change",
    "account_preferences_save",
    "account_profile_save",
    "account_sessions_revoke",
    "account_view",
    "account_watched_clear",
    "add_to_watchlist",
    "add_to_watched",
    "auth_google",
    "auth_google_callback",
    "bp",
    "filtered_movie_endpoint",
    "health_check",
    "home",
    "inject_csrf_token",
    "init_routes",
    "login_page",
    "login_submit",
    "logout",
    "metrics",
    "movie_detail",
    "next_movie",
    "previous_movie",
    "projection_state",
    "readiness_check",
    "register_page",
    "register_submit",
    "remove_from_watchlist",
    "remove_from_watched",
    "search_titles",
    "watched_list_page",
    "watchlist_page",
]
