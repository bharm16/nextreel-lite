from __future__ import annotations


def test_navigation_state_model_lives_in_domain_module():
    from nextreel.domain.navigation_state import NavigationState

    assert NavigationState.__module__ == "nextreel.domain.navigation_state"


def test_navigation_state_repository_lives_in_infra_module():
    from infra.navigation_state_repository import NavigationStateRepository

    assert NavigationStateRepository.__module__ == "infra.navigation_state_repository"


def test_navigation_state_service_lives_in_application_module():
    from nextreel.application.navigation_state_service import NavigationStateService

    assert NavigationStateService.__module__ == "nextreel.application.navigation_state_service"


def test_legacy_navigation_state_module_reexports_compatibility_surface():
    from infra.navigation_state import (
        NavigationState,
        NavigationStateRepository,
        NavigationStateService,
        NavigationStateStore,
    )

    assert NavigationState.__module__ == "nextreel.domain.navigation_state"
    assert NavigationStateRepository.__module__ == "infra.navigation_state_repository"
    assert NavigationStateService.__module__ == "nextreel.application.navigation_state_service"
    assert NavigationStateStore.__module__ == "nextreel.application.navigation_state_service"
