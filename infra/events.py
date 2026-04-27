"""Product-analytics event dispatch.

Thin wrapper around the PostHog Python SDK with two key properties:

1. **Non-blocking** — ``track_event`` and ``identify_user`` never block on
   network I/O. The PostHog SDK queues events on an in-memory ring buffer
   and flushes them on a background thread; this module wraps the calls in
   ``safe_emit``-style exception swallowing so a failed dispatch can never
   take down a request.

2. **Backend-swappable** — ``EventBackend`` is a Protocol. The default
   backends are ``LoggingEventBackend`` (emits structured JSON to the
   existing logger; no vendor) and ``PostHogEventBackend`` (PostHog cloud
   or self-hosted). A ``CompositeEventBackend`` multicasts to several at
   once for dev and migrations.

Picking up where Tier 1 left off: counters tell us *that* an action
happened; events tell us *who* did it and what surrounded it. Both layers
share the same call sites by design — the ``user_actions_total`` increment
and the ``track_event`` call sit next to each other so they can never
drift out of sync.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping, Protocol, runtime_checkable

from infra.secrets import secrets_manager
from logging_config import get_logger

logger = get_logger(__name__)

# Single source of truth for the upstream PostHog host. Used by both
# ``_init_analytics`` (SDK construction) and the reverse-proxy route
# (forward target) so they can never drift apart on the default value.
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"


# ── Anonymous distinct_id derivation ─────────────────────────────────


def anon_distinct_id(session_id: str | None) -> str:
    """Hash a navigation session_id into a stable anonymous distinct_id.

    The raw ``session_id`` is a credential — it authorizes navigation
    state mutations against ``user_navigation_state`` and is bound to
    the first-party session cookie. Sending it to PostHog would move
    that credential into a third-party log retention zone.

    Hashing with the app's ``FLASK_SECRET_KEY`` produces an opaque,
    deployment-stable identifier so the same anonymous browser always
    gets the same PostHog ID across requests (preserving funnel
    grouping) without leaking the underlying session token.

    Returns ``""`` when ``session_id`` is empty so callers can pipe
    straight into ``track_event(...)`` (which no-ops on empty IDs).
    """
    if not session_id:
        return ""
    secret = secrets_manager.get_secret("FLASK_SECRET_KEY") or ""
    digest = hmac.new(
        secret.encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return "anon-" + digest.hex()[:16]


# ── Backend protocol ─────────────────────────────────────────────────


@runtime_checkable
class EventBackend(Protocol):
    """Pluggable event-dispatch sink.

    Implementations must be safe to call from async request handlers
    without awaiting on network I/O. Failures must be swallowed —
    metric/event emission can never break a request.
    """

    def capture(
        self,
        distinct_id: str,
        event: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        ...

    def identify(
        self,
        distinct_id: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        ...

    def alias(self, previous_id: str, distinct_id: str) -> None:
        ...

    def shutdown(self) -> None:
        ...


# ── Logging backend (no vendor) ──────────────────────────────────────


class LoggingEventBackend:
    """Emits events as structured JSON logs.

    The existing JSONFormatter in ``logging_config`` will pick these up
    and, if Loki shipping is enabled, they end up in Loki where LogQL
    can query them. Useful in dev and as a no-vendor fallback.
    """

    def capture(
        self,
        distinct_id: str,
        event: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        logger.info(
            "analytics_event %s for %s",
            event,
            distinct_id,
            extra={
                "analytics_event": event,
                "analytics_distinct_id": distinct_id,
                "analytics_properties": _safe_json(properties or {}),
            },
        )

    def identify(
        self,
        distinct_id: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        logger.info(
            "analytics_identify %s",
            distinct_id,
            extra={
                "analytics_distinct_id": distinct_id,
                "analytics_properties": _safe_json(properties or {}),
            },
        )

    def alias(self, previous_id: str, distinct_id: str) -> None:
        logger.info(
            "analytics_alias %s -> %s",
            previous_id,
            distinct_id,
            extra={
                "analytics_alias_previous_id": previous_id,
                "analytics_distinct_id": distinct_id,
            },
        )

    def shutdown(self) -> None:
        # Logging handlers are shut down by the standard logging module;
        # nothing to do here.
        return None


def _safe_json(payload: Mapping[str, Any]) -> str:
    """JSON-encode a property dict, falling back to repr on any error.

    Some property values (datetime, Decimal, etc.) aren't JSON-serializable
    by default. We never want a malformed property dict to drop the log
    record, so we coerce on failure rather than raising.
    """
    try:
        return json.dumps(dict(payload), default=str)
    except Exception:  # pragma: no cover - defensive
        return repr(dict(payload))


# ── PostHog backend ──────────────────────────────────────────────────


class PostHogEventBackend:
    """Wraps the PostHog Python SDK.

    The SDK is sync at the call site but non-blocking: ``capture()``
    appends to an in-memory queue and a background daemon thread
    flushes it. Calling from an async request handler is safe.

    The SDK also handles retries, gzip, and batching internally — we
    don't add our own. We do wrap each call in a try/except because
    PostHog occasionally raises on bad property shapes or queue
    overflows, and we never want those to surface as 500s.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def capture(
        self,
        distinct_id: str,
        event: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            self._client.capture(
                event,
                distinct_id=distinct_id,
                properties=properties or {},
            )
        except Exception as exc:
            logger.debug("posthog capture failed: %s", exc)

    def identify(
        self,
        distinct_id: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            self._client.identify(
                distinct_id=distinct_id,
                properties=properties or {},
            )
        except Exception as exc:
            logger.debug("posthog identify failed: %s", exc)

    def alias(self, previous_id: str, distinct_id: str) -> None:
        try:
            self._client.alias(
                previous_id=previous_id,
                distinct_id=distinct_id,
            )
        except Exception as exc:
            logger.debug("posthog alias failed: %s", exc)

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception as exc:
            logger.debug("posthog shutdown failed: %s", exc)


def build_posthog_backend(
    *,
    project_api_key: str,
    host: str = DEFAULT_POSTHOG_HOST,
    flush_at: int = 100,
    flush_interval: float = 0.5,
    timeout: float = 15.0,
    max_retries: int = 3,
) -> PostHogEventBackend | None:
    """Construct a PostHog-backed event backend, or ``None`` on failure.

    Two failure modes degrade gracefully to the logging backend rather
    than crashing app startup:

    1. ``ImportError`` — SDK isn't installed.
    2. Any other ``Exception`` from the constructor — the SDK rejected
       a kwarg (e.g., the v3.7+ → v4 transition removes one of these
       options). Logging-only is strictly better than a startup crash.
    """
    try:
        from posthog import Posthog  # local import so missing dep != hard fail
    except ImportError:
        logger.warning(
            "PostHog SDK not installed; falling back to logging-only event backend"
        )
        return None

    try:
        client = Posthog(
            project_api_key=project_api_key,
            host=host,
            flush_at=flush_at,
            flush_interval=flush_interval,
            timeout=timeout,
            max_retries=max_retries,
            # Don't capture exceptions automatically — Sentry-style. Our
            # routes already handle their own error reporting via Prometheus
            # ``application_errors_total`` and structured logs.
            enable_exception_autocapture=False,
            # Disable GeoIP enrichment server-side — we don't ship IPs to
            # PostHog from the Python SDK, so the GeoIP feature has nothing
            # to enrich. Reduces noise.
            disable_geoip=True,
        )
    except Exception as exc:
        logger.warning(
            "PostHog SDK constructor rejected kwargs (%s); "
            "falling back to logging-only event backend",
            exc,
        )
        return None
    return PostHogEventBackend(client)


# ── Composite (multicast) backend ────────────────────────────────────


class CompositeEventBackend:
    """Fan out one event to multiple backends.

    Useful in dev (log + PostHog so you can debug taxonomy locally) or
    during migration (old + new vendor side-by-side for verification).
    """

    def __init__(self, backends: list[EventBackend]) -> None:
        self._backends = backends

    def capture(
        self,
        distinct_id: str,
        event: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        for backend in self._backends:
            backend.capture(distinct_id, event, properties)

    def identify(
        self,
        distinct_id: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        for backend in self._backends:
            backend.identify(distinct_id, properties)

    def alias(self, previous_id: str, distinct_id: str) -> None:
        for backend in self._backends:
            backend.alias(previous_id, distinct_id)

    def shutdown(self) -> None:
        for backend in self._backends:
            backend.shutdown()


# ── Module-level dispatch ────────────────────────────────────────────
# A single backend is configured at app startup via ``configure_event_backend``;
# request handlers call the module-level ``track_event``/``identify_user``
# functions and don't need to know which backend is active.

_backend: EventBackend | None = None


def configure_event_backend(backend: EventBackend | None) -> None:
    """Install the active event backend. Pass ``None`` to disable."""
    global _backend
    _backend = backend


def get_event_backend() -> EventBackend | None:
    return _backend


def shutdown_event_backend() -> None:
    """Flush and stop the active event backend, then clear the global.

    Called from app lifespan teardown. Safe to call when no backend is
    configured.
    """
    global _backend
    backend = _backend
    if backend is None:
        return
    try:
        backend.shutdown()
    finally:
        _backend = None


def track_event(
    distinct_id: str | None,
    event: str,
    properties: Mapping[str, Any] | None = None,
) -> None:
    """Fire-and-forget event capture.

    ``distinct_id`` is the stable identifier — the application user_id
    for authenticated users, or the navigation-state session_id for
    anonymous users. Passing ``None`` is silently a no-op (caller
    didn't have any identifier yet). The outer try/except is defence-in-
    depth — backends already swallow, but a misbehaving custom backend
    must not be able to take down a request.
    """
    if _backend is None or not distinct_id:
        return
    try:
        _backend.capture(distinct_id, event, properties)
    except Exception as exc:  # pragma: no cover - last-resort safety net
        logger.debug("track_event failed: %s", exc)


def identify_user(
    distinct_id: str,
    properties: Mapping[str, Any] | None = None,
) -> None:
    """Set or update user-level properties on a distinct_id.

    Called on signup, login, and OAuth completion to bind a previously
    anonymous distinct_id to a stable user identity and attach the
    properties (auth_provider, signup_at) that drive cohort breakdowns.
    """
    if _backend is None or not distinct_id:
        return
    try:
        _backend.identify(distinct_id, properties)
    except Exception as exc:  # pragma: no cover - last-resort safety net
        logger.debug("identify_user failed: %s", exc)


def alias_user(previous_id: str, distinct_id: str) -> None:
    """Alias an anonymous distinct_id to a logged-in user_id.

    Without this, server-side events fired between signup/login and the
    next browser pageview live under the anonymous ID forever; PostHog
    never learns they belong to the same person. Browser-side
    ``posthog.identify(user_id)`` performs the merge on the *next*
    pageview, but the events captured in the meantime stay orphaned.

    The canonical auth-boundary sequence (alias → identify → track) is
    encapsulated in :func:`bind_authenticated_identity` — call sites
    should prefer that helper over composing the three calls by hand.
    """
    if _backend is None or not previous_id or not distinct_id:
        return
    try:
        _backend.alias(previous_id, distinct_id)
    except Exception as exc:  # pragma: no cover - last-resort safety net
        logger.debug("alias_user failed: %s", exc)


def bind_authenticated_identity(
    *,
    anon_id: str,
    user_id: str,
    event: str,
    user_properties: Mapping[str, Any],
    event_properties: Mapping[str, Any] | None = None,
) -> None:
    """Run the canonical alias → identify → track sequence at an auth boundary.

    Order is load-bearing: PostHog only merges the pre-auth funnel into
    the user's identity if the alias arrives *before* the first identify+
    capture pair under the new user_id.

    ``user_properties`` is set on the person (cohort dimensions like
    ``auth_provider``); ``event_properties`` is attached to ``event``
    (defaults to ``user_properties`` when omitted, since most callers
    want the same auth_provider on both).
    """
    alias_user(anon_id, user_id)
    identify_user(user_id, user_properties)
    track_event(user_id, event, event_properties if event_properties is not None else user_properties)


__all__ = [
    "CompositeEventBackend",
    "DEFAULT_POSTHOG_HOST",
    "EventBackend",
    "LoggingEventBackend",
    "PostHogEventBackend",
    "alias_user",
    "anon_distinct_id",
    "bind_authenticated_identity",
    "build_posthog_backend",
    "configure_event_backend",
    "get_event_backend",
    "identify_user",
    "shutdown_event_backend",
    "track_event",
]
