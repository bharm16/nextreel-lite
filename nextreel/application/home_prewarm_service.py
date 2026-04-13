from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, MutableMapping

from infra.metrics import home_prewarm_failed_total
from nextreel.domain.navigation_state import NavigationState
from infra.time_utils import env_bool, env_float
from logging_config import get_logger
from nextreel.application.movie_navigator import MovieNavigator

logger = get_logger(__name__)


def nav_dual_write_active() -> bool:
    return env_bool("NAV_STATE_DUAL_WRITE_ENABLED", True)


class HomePrewarmService:
    def __init__(self) -> None:
        self._inflight_by_session: dict[str, asyncio.Task[Any]] = {}

    def _remember_task(self, session_id: str, task: asyncio.Task[Any]) -> None:
        self._inflight_by_session[session_id] = task

        def _cleanup(completed: asyncio.Task[Any]) -> None:
            if self._inflight_by_session.get(session_id) is completed:
                self._inflight_by_session.pop(session_id, None)

        task.add_done_callback(_cleanup)

    def _inflight_task(self, session_id: str) -> asyncio.Task[Any] | None:
        task = self._inflight_by_session.get(session_id)
        if task is None or task.done():
            return None
        return task

    async def wait_for_session(self, session_id: str) -> bool:
        task = self._inflight_task(session_id)
        if task is None:
            return False
        try:
            await task
        except Exception:
            pass
        return True

    def _schedule_background_prewarm(
        self,
        *,
        session_id: str,
        background_scheduler: Callable[[Awaitable[Any]], Any] | None,
        schedule_background: Callable[[Awaitable[Any]], bool],
        factory: Callable[[], Awaitable[Any]],
    ) -> asyncio.Task[Any] | None:
        existing = self._inflight_task(session_id)
        if existing is not None:
            return existing

        if background_scheduler is not None:
            coro = factory()
            try:
                task = background_scheduler(coro)
            except Exception:
                coro.close()
                return None
            if isinstance(task, asyncio.Task):
                self._remember_task(session_id, task)
                return task

        scheduled = schedule_background(factory())
        return None if scheduled else None

    async def prewarm(
        self,
        *,
        state: NavigationState | None,
        navigator: MovieNavigator | None,
        legacy_session: MutableMapping[str, Any] | None,
        background_scheduler: Callable[[Awaitable[Any]], Any] | None,
        schedule_background: Callable[[Awaitable[Any]], bool],
    ) -> None:
        if not state or state.queue or navigator is None:
            return

        session_id = state.session_id

        async def _bg_prewarm() -> None:
            try:
                await navigator.prewarm_queue(
                    session_id,
                    legacy_session=None,
                    current_state=None,
                )
            except Exception as exc:
                home_prewarm_failed_total.inc()
                logger.warning(
                    "Background home prewarm failed for %s: %s",
                    session_id,
                    exc,
                )

        dual_write_active = nav_dual_write_active()
        if background_scheduler is not None and not dual_write_active:
            self._schedule_background_prewarm(
                session_id=session_id,
                background_scheduler=background_scheduler,
                schedule_background=schedule_background,
                factory=lambda: _bg_prewarm(),
            )
            return

        prewarm_timeout = env_float("PREWARM_TIMEOUT_SECONDS", 0.1)
        try:
            await asyncio.wait_for(
                navigator.prewarm_queue(
                    state.session_id,
                    legacy_session=legacy_session,
                    current_state=state,
                ),
                timeout=prewarm_timeout,
            )
        except asyncio.TimeoutError:
            logger.info(
                "prewarm_queue exceeded %.2fs timeout; skipping prewarm",
                prewarm_timeout,
            )
            if background_scheduler is not None:
                self._schedule_background_prewarm(
                    session_id=session_id,
                    background_scheduler=background_scheduler,
                    schedule_background=schedule_background,
                    factory=lambda: _bg_prewarm(),
                )
        except Exception as exc:
            home_prewarm_failed_total.inc()
            logger.warning("Home prewarm failed for %s: %s", state.session_id, exc)
