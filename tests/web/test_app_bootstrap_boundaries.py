from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_movie_manager_factory_composes_injected_dependencies():
    from nextreel.bootstrap.movie_manager_factory import build_movie_manager

    class Manager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.projection_coordinator = kwargs["projection_store"].coordinator

    db_pool = MagicMock()
    tmdb_helper = MagicMock()
    candidate_store = MagicMock()
    projection_store = MagicMock()
    projection_store.coordinator = MagicMock()
    watched_store = MagicMock()
    renderer = MagicMock()
    prewarm = MagicMock()

    manager = build_movie_manager(
        {"host": "localhost"},
        db_pool_cls=MagicMock(return_value=db_pool),
        tmdb_helper_cls=MagicMock(return_value=tmdb_helper),
        candidate_store_cls=MagicMock(return_value=candidate_store),
        projection_store_cls=MagicMock(return_value=projection_store),
        watched_store_cls=MagicMock(return_value=watched_store),
        renderer_cls=MagicMock(return_value=renderer),
        home_prewarm_service_cls=MagicMock(return_value=prewarm),
        movie_manager_cls=Manager,
    )

    assert manager.kwargs == {
        "db_config": {"host": "localhost"},
        "db_pool": db_pool,
        "tmdb_helper": tmdb_helper,
        "candidate_store": candidate_store,
        "projection_store": projection_store,
        "watched_store": watched_store,
        "renderer": renderer,
        "home_prewarm_service": prewarm,
    }


def test_resolve_redis_url_uses_local_default(monkeypatch):
    from infra.redis_runtime import resolve_redis_url

    monkeypatch.delenv("REDIS_URL", raising=False)

    assert resolve_redis_url(environment="development") == "redis://localhost:6379"


def test_resolve_redis_url_requires_production_host_and_port(monkeypatch):
    from infra.redis_runtime import resolve_redis_url

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_HOST", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_PORT", raising=False)

    with pytest.raises(RuntimeError):
        resolve_redis_url(environment="production")


def test_resolve_redis_url_prefers_redis_url_in_production(monkeypatch):
    from infra.redis_runtime import resolve_redis_url

    monkeypatch.setenv("REDIS_URL", "redis://default:secret@redis.railway.internal:6379")
    monkeypatch.setenv("UPSTASH_REDIS_HOST", "upstash.example.com")
    monkeypatch.setenv("UPSTASH_REDIS_PORT", "6379")
    monkeypatch.setenv("UPSTASH_REDIS_PASSWORD", "ignored")

    assert (
        resolve_redis_url(environment="production")
        == "redis://default:secret@redis.railway.internal:6379"
    )


def test_resolve_redis_url_falls_back_to_upstash_when_redis_url_absent(monkeypatch):
    from infra.redis_runtime import resolve_redis_url

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("UPSTASH_REDIS_HOST", "upstash.example.com")
    monkeypatch.setenv("UPSTASH_REDIS_PORT", "6379")
    monkeypatch.setenv("UPSTASH_REDIS_PASSWORD", "pw")

    assert (
        resolve_redis_url(environment="production")
        == "rediss://:pw@upstash.example.com:6379"
    )


@pytest.mark.asyncio
async def test_runtime_job_queue_forwards_enqueue_kwargs():
    from infra.job_queue import RuntimeJobQueue

    pool = MagicMock()
    pool.enqueue_job = AsyncMock(return_value=object())
    create_pool = AsyncMock(return_value=pool)
    redis_settings = SimpleNamespace(from_dsn=MagicMock(return_value="settings"))
    app = SimpleNamespace(
        arq_redis=None,
        redis_available=True,
        redis_url="redis://localhost:6379",
        worker_available=False,
    )
    queue = RuntimeJobQueue(
        app,
        create_pool_fn=create_pool,
        redis_settings_cls=redis_settings,
    )

    await queue.enqueue_runtime_job("enrich_projection", "tt1", _job_id="enrich:tt1")

    create_pool.assert_awaited_once_with("settings")
    redis_settings.from_dsn.assert_called_once_with("redis://localhost:6379")
    pool.enqueue_job.assert_awaited_once_with(
        "enrich_projection",
        "tt1",
        _job_id="enrich:tt1",
    )
    assert app.worker_available is True


@pytest.mark.asyncio
async def test_runtime_job_queue_returns_none_when_pool_unavailable():
    from infra.job_queue import RuntimeJobQueue

    app = SimpleNamespace(
        arq_redis=None,
        redis_available=False,
        redis_url=None,
        worker_available=False,
    )
    queue = RuntimeJobQueue(app, create_pool_fn=AsyncMock(), redis_settings_cls=MagicMock())

    result = await queue.enqueue_runtime_job("refresh_movie_candidates")

    assert result is None


def test_request_context_builds_test_navigation_state():
    from nextreel.web.request_context import build_test_navigation_state

    state = build_test_navigation_state()

    assert state.csrf_token == "test-csrf-token"
    assert state.session_id
    assert state.filters["exclude_watched"] is True


def test_navigation_cookie_max_age_tracks_configured_session_duration():
    from nextreel.web.app import _navigation_cookie_max_age

    assert _navigation_cookie_max_age({"MAX_SESSION_DURATION_HOURS": 24}) == 24 * 60 * 60


def test_navigation_cookie_max_age_uses_safe_fallback_for_invalid_duration():
    from nextreel.domain.navigation_state import SESSION_COOKIE_MAX_AGE
    from nextreel.web.app import _navigation_cookie_max_age

    assert _navigation_cookie_max_age({"MAX_SESSION_DURATION_HOURS": "invalid"}) == (
        SESSION_COOKIE_MAX_AGE
    )


@pytest.mark.asyncio
async def test_lifecycle_startup_schedules_candidate_refresh(app, monkeypatch):
    from nextreel.web.lifecycle import register_lifecycle_handlers

    ensure_started = AsyncMock()
    movie_manager = SimpleNamespace(
        db_pool=MagicMock(),
        candidate_store=SimpleNamespace(latest_refresh_at=AsyncMock(return_value=None)),
    )
    app.background_tasks = set()
    app.enqueue_runtime_job = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "nextreel.web.lifecycle.ensure_movie_candidates_fulltext_index",
        AsyncMock(),
    )

    register_lifecycle_handlers(
        app,
        ensure_movie_manager_started=ensure_started,
        movie_manager=movie_manager,
    )
    startup_hook = next(func for func in app.before_serving_funcs if func.__name__ == "startup")

    await startup_hook()

    ensure_started.assert_awaited_once()
    app.enqueue_runtime_job.assert_awaited_once_with("refresh_movie_candidates")
