from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from quart import g, make_response, request, session

from infra.metrics import application_errors_total
from infra.filter_normalizer import default_filter_state
from infra.time_utils import utcnow
from nextreel.domain.navigation_state import (
    SESSION_COOKIE_MAX_AGE,
    SESSION_COOKIE_NAME,
    NavigationState,
)
from infra.security_headers import add_security_headers
from infra.time_utils import env_int
from logging_config import get_logger
from nextreel.web.middleware import _CORRELATION_LOG_SKIP_PREFIXES, add_correlation_id

logger = get_logger(__name__)

_slow_log_counter = 0


def maybe_log_slow_request(*, endpoint, elapsed, session_id, correlation_id) -> None:
    """Log a slow request iff it wins the 1-in-N counter-based sample."""
    global _slow_log_counter
    sample_rate = max(1, env_int("SLOW_LOG_SAMPLE_RATE", 1))
    _slow_log_counter += 1
    if _slow_log_counter % sample_rate != 0:
        return
    logger.warning(
        "Slow request: %s took %.2fs (state: %s, correlation: %s)",
        endpoint,
        elapsed,
        session_id,
        correlation_id,
    )


def build_test_navigation_state() -> NavigationState:
    now = utcnow()
    return NavigationState(
        session_id=uuid4().hex,
        version=1,
        csrf_token="test-csrf-token",
        filters=default_filter_state(),
        current_tconst=None,
        current_ref=None,
        queue=[],
        prev=[],
        future=[],
        seen=[],
        created_at=now,
        last_activity_at=now,
        expires_at=now,
    )


def register_request_context_handlers(app, *, ensure_movie_manager_started) -> None:
    @app.before_request
    async def before_request():
        try:
            await add_correlation_id()

            if any(request.path.startswith(p) for p in _CORRELATION_LOG_SKIP_PREFIXES):
                return

            if app.config.get("TESTING") and app.navigation_state_store is None:
                g.navigation_state = build_test_navigation_state()
                g.set_nr_sid_cookie = False
                return

            await ensure_movie_manager_started()

            legacy_session = session if app.config.get("SESSION_REDIS") else None
            state, needs_cookie = await app.navigation_state_store.load_for_request(
                request.cookies.get(SESSION_COOKIE_NAME),
                legacy_session=legacy_session,
            )
            g.navigation_state = state
            g.set_nr_sid_cookie = (
                needs_cookie or request.cookies.get(SESSION_COOKIE_NAME) != state.session_id
            )
        except (asyncio.CancelledError, SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            logger.error("Error loading navigation state: %s", exc, exc_info=True)
            try:
                application_errors_total.labels(
                    error_type="navigation_state_load_failure",
                    endpoint=request.endpoint or "unknown",
                ).inc()
            except Exception:  # pragma: no cover - metrics never break requests
                pass
            return await make_response(("Service temporarily unavailable", 503))

    @app.after_request
    async def after_request(response):
        correlation_id = g.get("correlation_id") if hasattr(g, "get") else None
        if correlation_id:
            response.headers["X-Correlation-ID"] = correlation_id

        if hasattr(g, "start_time"):
            elapsed = time.time() - g.start_time
            if elapsed > 1.0:
                maybe_log_slow_request(
                    endpoint=request.endpoint,
                    elapsed=elapsed,
                    session_id=getattr(getattr(g, "navigation_state", None), "session_id", None),
                    correlation_id=correlation_id,
                )
            response.headers["X-Response-Time"] = f"{elapsed:.3f}"

        state = getattr(g, "navigation_state", None)
        if state and getattr(g, "set_nr_sid_cookie", False):
            response.set_cookie(
                SESSION_COOKIE_NAME,
                state.session_id,
                max_age=SESSION_COOKIE_MAX_AGE,
                secure=app.config.get("SESSION_COOKIE_SECURE", False),
                httponly=True,
                samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
                domain=app.config.get("SESSION_COOKIE_DOMAIN"),
                path="/",
            )

        return await add_security_headers(response)
