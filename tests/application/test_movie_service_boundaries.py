from __future__ import annotations


def test_home_prewarm_service_lives_outside_movie_manager_module():
    from nextreel.application.home_prewarm_service import HomePrewarmService

    assert HomePrewarmService.__module__ == "nextreel.application.home_prewarm_service"


def test_movie_service_reexports_home_prewarm_service_for_compatibility():
    from nextreel.application.home_prewarm_service import HomePrewarmService
    from nextreel.application.movie_service import HomePrewarmService as CompatHomePrewarmService

    assert CompatHomePrewarmService is HomePrewarmService
