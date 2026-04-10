"""Feature-namespaced route modules built on the shared blueprint."""

from nextreel.web.routes.auth import (
    auth_apple,
    auth_apple_callback,
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
    set_filters,
)
from nextreel.web.routes.ops import health_check, metrics, readiness_check
from nextreel.web.routes.watched import (
    add_to_watched,
    remove_from_watched,
    watched_list_page,
)
from nextreel.web.routes.shared import bp, init_routes

__all__ = [
    "add_to_watched",
    "auth_apple",
    "auth_apple_callback",
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
    "readiness_check",
    "register_page",
    "register_submit",
    "remove_from_watched",
    "set_filters",
    "watched_list_page",
]
