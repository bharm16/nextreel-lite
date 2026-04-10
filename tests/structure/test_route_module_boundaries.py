import importlib


def test_feature_route_handlers_are_defined_in_feature_modules():
    expectations = {
        "nextreel.web.routes.auth": [
            "inject_csrf_token",
            "login_page",
            "login_submit",
            "register_page",
            "register_submit",
            "logout",
            "auth_google",
            "auth_google_callback",
            "auth_apple",
            "auth_apple_callback",
        ],
        "nextreel.web.routes.movies": [
            "home",
            "movie_detail",
        ],
        "nextreel.web.routes.navigation": [
            "next_movie",
            "previous_movie",
            "set_filters",
            "filtered_movie_endpoint",
        ],
        "nextreel.web.routes.ops": [
            "health_check",
            "metrics",
            "readiness_check",
        ],
        "nextreel.web.routes.watched": [
            "watched_list_page",
            "add_to_watched",
            "remove_from_watched",
        ],
    }

    for module_name, handler_names in expectations.items():
        module = importlib.import_module(module_name)
        for handler_name in handler_names:
            handler = getattr(module, handler_name)
            assert handler.__module__ == module_name


def test_public_routes_surface_reexports_feature_handlers():
    routes = importlib.import_module("routes")
    auth_routes = importlib.import_module("nextreel.web.routes.auth")
    movie_routes = importlib.import_module("nextreel.web.routes.movies")
    navigation_routes = importlib.import_module("nextreel.web.routes.navigation")
    ops_routes = importlib.import_module("nextreel.web.routes.ops")
    watched_routes = importlib.import_module("nextreel.web.routes.watched")

    assert routes.login_submit is auth_routes.login_submit
    assert routes.movie_detail is movie_routes.movie_detail
    assert routes.next_movie is navigation_routes.next_movie
    assert routes.readiness_check is ops_routes.readiness_check
    assert routes.watched_list_page is watched_routes.watched_list_page
