import importlib


def test_new_runtime_package_modules_are_importable():
    modules = [
        "nextreel.application.auth_flows",
        "nextreel.application.movie_navigator",
        "nextreel.application.movie_service",
        "nextreel.domain.filter_contracts",
        "nextreel.web.app",
        "nextreel.web.middleware",
        "nextreel.web.movie_renderer",
        "nextreel.web.route_services",
        "nextreel.web.routes",
        "nextreel.web.routes.auth",
        "nextreel.web.routes.movies",
        "nextreel.web.routes.navigation",
        "nextreel.web.routes.ops",
        "nextreel.web.routes.watched",
        "nextreel.workers.worker",
    ]

    for module_name in modules:
        assert importlib.import_module(module_name) is not None


def test_app_entry_point_delegates_to_nextreel_web():
    package_create_app = importlib.import_module("nextreel.web.app").create_app
    entry_create_app = importlib.import_module("app").create_app
    assert entry_create_app is package_create_app
