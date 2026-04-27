import asyncio
import logging
import os
import re
import socket
from pathlib import Path

# --- Early local-env bootstrap -------------------------------------------
# Must run BEFORE ``import settings`` so that ``get_environment()``'s first
# (cached) call sees NEXTREEL_ENV=development when no env var is set in the
# shell.  In production the env var is always set by the deploy pipeline, so
# this block is a no-op there.
if not os.environ.get("NEXTREEL_ENV") and not os.environ.get("FLASK_ENV"):
    from scripts.local_env_setup import setup_local_environment

    setup_local_environment()
# -------------------------------------------------------------------------

from quart import Quart, got_request_exception, request
from werkzeug.exceptions import HTTPException

import settings
from infra.metrics import (
    MetricsCollector,
    application_errors_total,
    bucket_error_type,
    setup_metrics_middleware,
    tt_url_404_total,
)
from nextreel.domain.navigation_state import (
    SESSION_COOKIE_MAX_AGE,
    SESSION_COOKIE_NAME,
)
from infra.secrets import secrets_manager
from infra.time_utils import env_int
from logging_config import get_logger, setup_logging
from nextreel.bootstrap.movie_manager_factory import (
    build_movie_manager as _compose_movie_manager,
)
from nextreel.application.movie_service import MovieManager
from infra.job_queue import install_runtime_job_queue
from infra.redis_runtime import setup_redis_runtime as _setup_redis
from nextreel.web.lifecycle import register_lifecycle_handlers
from nextreel.web.request_context import register_request_context_handlers
from nextreel.web.routes import bp as routes_bp, init_routes


class FixedQuart(Quart):
    """Quart subclass ensuring Flask compatibility keys."""

    default_config = dict(Quart.default_config)
    default_config.setdefault("PROVIDE_AUTOMATIC_OPTIONS", True)


logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Legacy IMDb-tconst URL patterns that should now 404 post-migration to the
# public_id slug scheme. We track 404s on these specific shapes so we can see
# how many bookmarks / external links still point at the dead URLs.
_LEGACY_TT_PATH_RE = re.compile(
    r"/(?:movie|watched/(?:add|remove)|watchlist/(?:add|remove)|api/projection-state)/tt\d+"
)


def _navigation_cookie_max_age(config) -> int:
    try:
        max_session_hours = int(config.get("MAX_SESSION_DURATION_HOURS", 0))
    except (TypeError, ValueError):
        return SESSION_COOKIE_MAX_AGE
    if max_session_hours <= 0:
        return SESSION_COOKIE_MAX_AGE
    return max_session_hours * 60 * 60


def _init_core(app):
    """Phase 1: Core app config and movie manager."""
    app.config.from_object(settings.Config())
    app.config["NR_SESSION_COOKIE_NAME"] = SESSION_COOKIE_NAME
    app.config["NR_SESSION_COOKIE_MAX_AGE"] = _navigation_cookie_max_age(app.config)

    # CSS cache-busting: use output.css mtime as version query param
    css_path = os.path.join(app.root_path, "static", "css", "output.css")
    app.config["CSS_VERSION"] = (
        str(int(os.path.getmtime(css_path))) if os.path.exists(css_path) else "1"
    )

    # Pass the locally-imported MovieManager so tests that patch
    # ``app.MovieManager`` intercept production composition. The bootstrap
    # factory keeps the rest of the wiring; this single explicit class lets
    # the test fixture pattern keep working.
    movie_manager = _compose_movie_manager(
        settings.Config.get_db_config(),
        movie_manager_cls=MovieManager,
    )
    app.movie_manager = movie_manager
    app.navigation_state_store = None
    app.shared_redis_pool = None
    app.arq_redis = None
    app.redis_url = None
    app.redis_available = False
    app.worker_available = False
    app.redis_cache = None
    app.secure_cache = None
    app.background_tasks = set()
    app.config["SESSION_REDIS"] = None

    # Wire a background-task scheduler so MovieManager (and, transitively, the
    # projection enrichment coordinator) can schedule best-effort background
    # work. Tasks are registered in app.background_tasks so shutdown drains
    # them. The coordinator may pass an already-scheduled asyncio.Task instead
    # of a coroutine — we accept both.
    def _schedule_background_task(awaitable):
        if isinstance(awaitable, asyncio.Task):
            task = awaitable
        else:
            task = asyncio.create_task(awaitable)
        app.background_tasks.add(task)
        task.add_done_callback(app.background_tasks.discard)
        return task

    movie_manager.attach_background_scheduler(_schedule_background_task)
    return movie_manager


def _init_oauth(app):
    """Phase 1b: OAuth client setup (optional — skipped if no credentials configured)."""
    google_client_id = os.getenv("GOOGLE_CLIENT_ID")
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_base = os.getenv("OAUTH_REDIRECT_BASE_URL", "http://127.0.0.1:5000")

    app.oauth_config = {
        "google_enabled": bool(google_client_id and google_client_secret),
        "google_client_id": google_client_id,
        "google_client_secret": google_client_secret,
        "redirect_base": redirect_base,
    }


def _init_metrics(app, movie_manager):
    """Phase 2: Metrics collector and route wiring."""
    metrics_collector = MetricsCollector(db_pool=movie_manager.db_pool, movie_manager=movie_manager)
    app.metrics_collector = metrics_collector
    init_routes(app, movie_manager, metrics_collector)
    return metrics_collector


def _make_manager_starter(app, movie_manager):
    """Phase 3: Lazy MovieManager startup guard."""
    started = False
    lock = asyncio.Lock()

    async def ensure_movie_manager_started():
        nonlocal started
        if started:
            return

        async with lock:
            if started:
                return

            await movie_manager.start()
            app.navigation_state_store = movie_manager.navigation_state_store
            started = True
            logger.info("MovieManager started successfully")

    return ensure_movie_manager_started


def create_app():
    setup_logging(log_level=logging.INFO)
    if not secrets_manager.validate_all_secrets():
        raise RuntimeError("Failed to validate required secrets. Check logs for details.")

    app = FixedQuart(
        __name__,
        root_path=str(_REPO_ROOT),
        template_folder="templates",
        static_folder="static",
    )
    movie_manager = _init_core(app)
    _init_oauth(app)
    metrics_collector = _init_metrics(app, movie_manager)
    ensure_movie_manager_started = _make_manager_starter(app, movie_manager)

    @app.before_serving
    async def setup_redis():
        await _setup_redis(app)

    install_runtime_job_queue(app, movie_manager)
    register_request_context_handlers(
        app,
        ensure_movie_manager_started=ensure_movie_manager_started,
    )
    register_lifecycle_handlers(
        app,
        ensure_movie_manager_started=ensure_movie_manager_started,
        movie_manager=movie_manager,
    )

    setup_metrics_middleware(app, metrics_collector)

    # Emit application_errors_total for uncaught non-HTTP exceptions.
    # HTTPExceptions (4xx like CSRF 403) are normal flow and NOT counted.
    # We use got_request_exception so Quart's own 500 rendering still runs.
    def _on_request_exception(sender, exception, **extra):
        try:
            if isinstance(exception, HTTPException):
                return
            # Bucket to an allow-list so dynamic exception classes cannot
            # explode label cardinality on application_errors_total.
            error_type = bucket_error_type(type(exception).__name__)
            endpoint = request.endpoint or "unknown"
            application_errors_total.labels(error_type=error_type, endpoint=endpoint).inc()
        except Exception:  # pragma: no cover - metrics must never break error path
            pass

    got_request_exception.connect(_on_request_exception, app)

    # Count 404s that hit legacy /movie/tt..., /watched/{add,remove}/tt...,
    # /watchlist/{add,remove}/tt..., or /api/projection-state/tt... URLs so we
    # can see how many bookmarks/external links still target the pre-migration
    # IMDb-tconst URL scheme. Always returns the default 404 response.
    @app.errorhandler(404)
    async def _on_404(error):  # noqa: ARG001 - Quart passes the exception
        try:
            if _LEGACY_TT_PATH_RE.search(request.path or ""):
                tt_url_404_total.inc()
        except Exception:  # pragma: no cover - metrics must never break error path
            pass
        return "Not found", 404

    app.register_blueprint(routes_bp)
    return app


def find_free_port(start_port=5000, host="127.0.0.1"):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((host, port)) != 0:
                return port
            port += 1


def main() -> None:
    app = create_app()
    port = env_int("PORT", find_free_port())
    logger.info("Starting development server on http://127.0.0.1:%s", port)
    app.run(host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
