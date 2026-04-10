"""Tests for the ProjectionState enum."""

from movies.projection_state import ENQUEUE_COOLDOWN, STALE_AFTER, ProjectionState


def test_ready_can_serve():
    assert ProjectionState.READY.can_serve() is True


def test_stale_can_serve():
    assert ProjectionState.STALE.can_serve() is True


def test_core_cannot_serve():
    assert ProjectionState.CORE.can_serve() is False


def test_failed_cannot_serve():
    assert ProjectionState.FAILED.can_serve() is False


def test_core_needs_enrichment():
    assert ProjectionState.CORE.needs_enrichment() is True


def test_stale_needs_enrichment():
    assert ProjectionState.STALE.needs_enrichment() is True


def test_failed_needs_enrichment():
    assert ProjectionState.FAILED.needs_enrichment() is True


def test_ready_does_not_need_enrichment():
    assert ProjectionState.READY.needs_enrichment() is False


def test_values_match_legacy_strings():
    assert ProjectionState.CORE.value == "core"
    assert ProjectionState.READY.value == "ready"
    assert ProjectionState.STALE.value == "stale"
    assert ProjectionState.FAILED.value == "failed"


def test_policy_constants():
    assert ENQUEUE_COOLDOWN.total_seconds() == 900  # 15 min
    assert STALE_AFTER.days == 7
