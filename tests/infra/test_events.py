"""Tests for the product-analytics event dispatcher."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import TEST_ENV

from infra.event_schema import (
    EVENT_LOGIN_SUCCEEDED,
    EVENT_SEARCH_PERFORMED,
    bucket_search_result_count,
)
from infra.events import (
    CompositeEventBackend,
    LoggingEventBackend,
    PostHogEventBackend,
    alias_user,
    anon_distinct_id,
    configure_event_backend,
    get_event_backend,
    identify_user,
    shutdown_event_backend,
    track_event,
)


@pytest.fixture(autouse=True)
def reset_backend():
    """Each test starts with no backend installed; restore on exit."""
    previous = get_event_backend()
    configure_event_backend(None)
    try:
        yield
    finally:
        configure_event_backend(previous)


# ── module-level dispatch ─────────────────────────────────────────────


def test_track_event_no_backend_is_noop():
    track_event("user-1", "any_event", {"foo": "bar"})  # must not raise


def test_track_event_dispatches_to_active_backend():
    backend = MagicMock(spec=LoggingEventBackend)
    configure_event_backend(backend)
    track_event("user-1", EVENT_LOGIN_SUCCEEDED, {"auth_provider": "email"})
    backend.capture.assert_called_once_with(
        "user-1", EVENT_LOGIN_SUCCEEDED, {"auth_provider": "email"}
    )


def test_track_event_swallows_backend_exceptions():
    backend = MagicMock(spec=LoggingEventBackend)
    backend.capture.side_effect = RuntimeError("boom")
    configure_event_backend(backend)
    track_event("user-1", EVENT_LOGIN_SUCCEEDED)  # must not raise


def test_track_event_with_no_distinct_id_is_noop():
    backend = MagicMock(spec=LoggingEventBackend)
    configure_event_backend(backend)
    track_event(None, EVENT_LOGIN_SUCCEEDED)
    track_event("", EVENT_LOGIN_SUCCEEDED)
    backend.capture.assert_not_called()


def test_identify_user_dispatches_to_backend():
    backend = MagicMock(spec=LoggingEventBackend)
    configure_event_backend(backend)
    identify_user("user-1", {"auth_provider": "google"})
    backend.identify.assert_called_once_with("user-1", {"auth_provider": "google"})


def test_identify_user_no_backend_is_noop():
    identify_user("user-1", {"auth_provider": "google"})  # must not raise


def test_shutdown_event_backend_calls_shutdown_and_clears():
    backend = MagicMock(spec=LoggingEventBackend)
    configure_event_backend(backend)
    shutdown_event_backend()
    backend.shutdown.assert_called_once()
    assert get_event_backend() is None


def test_shutdown_event_backend_with_no_backend_is_noop():
    shutdown_event_backend()  # must not raise


# ── LoggingEventBackend ───────────────────────────────────────────────


def test_logging_backend_capture_uses_logger(caplog):
    backend = LoggingEventBackend()
    with caplog.at_level("INFO", logger="infra.events"):
        backend.capture("user-1", EVENT_LOGIN_SUCCEEDED, {"k": "v"})
    # The formatted message includes the event name and distinct_id so plain-
    # text log readers can identify the line at a glance; the structured
    # ``extra`` payload still carries the parsed fields for Loki/JSON consumers.
    assert any(
        EVENT_LOGIN_SUCCEEDED in r.getMessage() and "user-1" in r.getMessage()
        for r in caplog.records
    )


def test_logging_backend_identify_uses_logger(caplog):
    backend = LoggingEventBackend()
    with caplog.at_level("INFO", logger="infra.events"):
        backend.identify("user-1", {"auth_provider": "email"})
    assert any(
        "analytics_identify" in r.getMessage() and "user-1" in r.getMessage()
        for r in caplog.records
    )


def test_logging_backend_handles_non_json_serializable_properties(caplog):
    """A datetime/Decimal/etc. property must not blow up the dispatch."""
    from datetime import datetime

    backend = LoggingEventBackend()
    with caplog.at_level("INFO", logger="infra.events"):
        backend.capture("user-1", "x", {"created": datetime(2026, 1, 1)})
    # It should still log something — coercion happens via default=str.
    assert any("analytics_event" in r.getMessage() for r in caplog.records)


def test_logging_backend_shutdown_is_noop():
    LoggingEventBackend().shutdown()


# ── PostHogEventBackend ───────────────────────────────────────────────


def test_posthog_backend_passes_through_to_client():
    client = MagicMock()
    backend = PostHogEventBackend(client)
    backend.capture("user-1", "evt", {"k": "v"})
    client.capture.assert_called_once_with(
        "evt", distinct_id="user-1", properties={"k": "v"}
    )


def test_posthog_backend_swallows_capture_exceptions():
    client = MagicMock()
    client.capture.side_effect = RuntimeError("posthog rejected")
    backend = PostHogEventBackend(client)
    backend.capture("user-1", "evt")  # must not raise


def test_posthog_backend_swallows_identify_exceptions():
    client = MagicMock()
    client.identify.side_effect = RuntimeError("posthog rejected")
    backend = PostHogEventBackend(client)
    backend.identify("user-1", {"k": "v"})  # must not raise


def test_posthog_backend_shutdown_calls_client_shutdown():
    client = MagicMock()
    backend = PostHogEventBackend(client)
    backend.shutdown()
    client.shutdown.assert_called_once()


def test_posthog_backend_swallows_shutdown_exceptions():
    client = MagicMock()
    client.shutdown.side_effect = RuntimeError("network")
    backend = PostHogEventBackend(client)
    backend.shutdown()  # must not raise


# ── CompositeEventBackend ─────────────────────────────────────────────


def test_composite_fans_out_capture():
    a, b = MagicMock(), MagicMock()
    composite = CompositeEventBackend([a, b])
    composite.capture("user-1", "evt", {"k": 1})
    a.capture.assert_called_once_with("user-1", "evt", {"k": 1})
    b.capture.assert_called_once_with("user-1", "evt", {"k": 1})


def test_composite_fans_out_identify():
    a, b = MagicMock(), MagicMock()
    composite = CompositeEventBackend([a, b])
    composite.identify("user-1", {"k": 1})
    a.identify.assert_called_once_with("user-1", {"k": 1})
    b.identify.assert_called_once_with("user-1", {"k": 1})


def test_composite_fans_out_shutdown():
    a, b = MagicMock(), MagicMock()
    CompositeEventBackend([a, b]).shutdown()
    a.shutdown.assert_called_once()
    b.shutdown.assert_called_once()


# ── Schema helpers ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, "0"),
        (-5, "0"),
        (1, "1-5"),
        (5, "1-5"),
        (6, "6-10"),
        (10, "6-10"),
        (11, "11+"),
        (10000, "11+"),
    ],
)
def test_bucket_search_result_count(count, expected):
    assert bucket_search_result_count(count) == expected


def test_event_search_performed_constant():
    """Constants exist so call sites don't pass raw strings."""
    assert EVENT_SEARCH_PERFORMED == "search_performed"
    assert EVENT_LOGIN_SUCCEEDED == "login_succeeded"


# ── anon_distinct_id ─────────────────────────────────────────────────


@pytest.fixture
def with_flask_secret():
    """Provide FLASK_SECRET_KEY so secrets_manager.get_secret() succeeds.

    ``anon_distinct_id`` HMACs against the app's secret key — without it
    in the environment, the secrets manager raises rather than silently
    using an empty key (which would defeat the security property).
    """
    with patch.dict(os.environ, TEST_ENV):
        yield


def test_anon_distinct_id_empty_input_returns_empty(with_flask_secret):
    """Empty input must produce empty output so callers can pipe straight
    into track_event (which no-ops on empty)."""
    assert anon_distinct_id("") == ""
    assert anon_distinct_id(None) == ""


def test_anon_distinct_id_is_deterministic_for_same_input(with_flask_secret):
    """Same session_id always produces the same opaque ID — required for
    PostHog funnel grouping to actually group anything."""
    a = anon_distinct_id("session-abc-123")
    b = anon_distinct_id("session-abc-123")
    assert a == b
    assert a.startswith("anon-")


def test_anon_distinct_id_differs_between_sessions(with_flask_secret):
    """Different session_ids must hash to different IDs (collision check)."""
    a = anon_distinct_id("session-aaa")
    b = anon_distinct_id("session-bbb")
    assert a != b


def test_anon_distinct_id_does_not_leak_raw_session_id(with_flask_secret):
    """The raw session_id must never appear in the output — that's the
    whole point of hashing."""
    raw = "secret-session-token-do-not-leak"
    out = anon_distinct_id(raw)
    assert raw not in out


# ── alias_user ───────────────────────────────────────────────────────


def test_alias_user_no_backend_is_noop():
    alias_user("anon-xyz", "user-1")  # must not raise


def test_alias_user_dispatches_to_backend():
    backend = MagicMock(spec=LoggingEventBackend)
    configure_event_backend(backend)
    alias_user("anon-xyz", "user-1")
    backend.alias.assert_called_once_with("anon-xyz", "user-1")


def test_alias_user_with_empty_id_is_noop():
    """Empty previous_id or distinct_id must not produce a malformed
    alias call — better to skip than to send junk to PostHog."""
    backend = MagicMock(spec=LoggingEventBackend)
    configure_event_backend(backend)
    alias_user("", "user-1")
    alias_user("anon-xyz", "")
    backend.alias.assert_not_called()


def test_alias_user_swallows_backend_exceptions():
    backend = MagicMock(spec=LoggingEventBackend)
    backend.alias.side_effect = RuntimeError("posthog rejected")
    configure_event_backend(backend)
    alias_user("anon-xyz", "user-1")  # must not raise


def test_logging_backend_alias_uses_logger(caplog):
    backend = LoggingEventBackend()
    with caplog.at_level("INFO", logger="infra.events"):
        backend.alias("anon-xyz", "user-1")
    assert any("anon-xyz" in r.getMessage() for r in caplog.records)


def test_posthog_backend_alias_passes_through_to_client():
    client = MagicMock()
    backend = PostHogEventBackend(client)
    backend.alias("anon-xyz", "user-1")
    client.alias.assert_called_once_with(previous_id="anon-xyz", distinct_id="user-1")


def test_posthog_backend_alias_swallows_exceptions():
    client = MagicMock()
    client.alias.side_effect = RuntimeError("posthog down")
    backend = PostHogEventBackend(client)
    backend.alias("anon-xyz", "user-1")  # must not raise


def test_composite_fans_out_alias():
    a, b = MagicMock(), MagicMock()
    composite = CompositeEventBackend([a, b])
    composite.alias("anon-xyz", "user-1")
    a.alias.assert_called_once_with("anon-xyz", "user-1")
    b.alias.assert_called_once_with("anon-xyz", "user-1")
