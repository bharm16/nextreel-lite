"""Projection enrichment orchestration and local task lifecycle."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from infra.metrics import (
    enrichment_backlog_drop_total,
    enrichment_timeout_total,
)
from infra.time_utils import utcnow
from logging_config import get_logger


def _safe_inc(counter, **labels) -> None:
    try:
        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:  # pragma: no cover - defensive
        pass
from movies.movie import Movie
from movies.projection_state import ENQUEUE_COOLDOWN, ProjectionState

if TYPE_CHECKING:
    from movies.projection_store import ProjectionStore

logger = get_logger(__name__)


class ProjectionEnrichmentCoordinator:
    """Owns enrichment orchestration outside the persistence layer."""

    # Cap concurrent in-process enrichment coroutines so a burst of cache
    # misses (e.g. a filter change touching many stale projections) cannot
    # exhaust the DB pool or trip the TMDb circuit breaker.
    LOCAL_ENRICHMENT_CONCURRENCY = 20

    # Cap pending in-process enrichment task creation so a burst can't
    # accumulate hundreds of coroutines waiting on the semaphore.
    LOCAL_ENRICHMENT_MAX_PENDING = 200

    # Overall ceiling on a single enrichment attempt. Headroom over the two
    # sequential 10s TMDb HTTP timeouts; anything longer is treated as a
    # failure so worker/semaphore slots free up.
    ENRICHMENT_TIMEOUT_SECONDS = 25.0

    def __init__(
        self,
        store: "ProjectionStore",
        tmdb_helper=None,
        enqueue_fn=None,
        local_concurrency: int | None = None,
        max_pending: int | None = None,
        background_scheduler=None,
    ) -> None:
        self.store = store
        self.tmdb_helper = tmdb_helper
        self.enqueue_fn = enqueue_fn
        self._local_enrichment_tconsts: set[str] = set()
        self._local_enrichment_tasks: set[asyncio.Task] = set()
        self._local_enrichment_semaphore = asyncio.Semaphore(
            local_concurrency or self.LOCAL_ENRICHMENT_CONCURRENCY
        )
        self._local_enrichment_max_pending = (
            max_pending if max_pending is not None else self.LOCAL_ENRICHMENT_MAX_PENDING
        )
        # In-flight map: tconst -> asyncio.Task running enrich_projection().
        # Guarded by a single coordinator-wide lock held only across
        # lookup+create+removal — never across the enrichment work itself.
        self._inflight_enrichment: dict[str, asyncio.Task] = {}
        self._inflight_lock = asyncio.Lock()
        # App-level background scheduler (wired from app.create_app). Lets
        # prefetch tasks register in app.background_tasks so they survive
        # request teardown and are drained on shutdown.
        self._background_scheduler = background_scheduler

    def attach_background_scheduler(self, scheduler) -> None:
        """Wire an app-owned background task scheduler."""
        self._background_scheduler = scheduler

    async def get_or_start_inflight(
        self,
        tconst: str,
        tmdb_id: int | None = None,
    ) -> asyncio.Task:
        """Atomically return an existing in-flight enrichment task for
        ``tconst`` or start a new one.

        The single coordinator lock is held only across map lookup and task
        creation/insertion — never across the enrichment work itself. A task
        is removed from the map on completion (success or failure) so a
        failed task does not poison later lookups.
        """
        async with self._inflight_lock:
            existing = self._inflight_enrichment.get(tconst)
            if existing is not None and not existing.done():
                return existing

            task = asyncio.create_task(
                self._run_inflight_enrichment(tconst, tmdb_id)
            )
            self._inflight_enrichment[tconst] = task

            # Register with the app-level background task set so the task is
            # tracked for shutdown drain and not GC'd if all awaiters go away.
            scheduler = self._background_scheduler
            if scheduler is not None:
                try:
                    scheduler(task)
                except Exception:  # pragma: no cover - defensive
                    pass
            else:
                self._local_enrichment_tasks.add(task)
                task.add_done_callback(self._local_enrichment_tasks.discard)

            return task

    async def _run_inflight_enrichment(
        self,
        tconst: str,
        tmdb_id: int | None,
    ):
        try:
            async with self._local_enrichment_semaphore:
                return await self.enrich_projection(tconst, known_tmdb_id=tmdb_id)
        finally:
            # Remove ourselves from the map so a subsequent request starts
            # fresh — even on failure, so a failed task does not poison
            # future lookups. From inside the task, ``task.done()`` still
            # reads False here, so we pop unconditionally. Any concurrent
            # awaiter already holds a reference to the returned task.
            async with self._inflight_lock:
                self._inflight_enrichment.pop(tconst, None)

    def has_inflight(self, tconst: str) -> bool:
        """Cheap non-locking check for an in-flight task (best-effort).

        Callers that need a correct check-then-act decision must use
        :meth:`maybe_enqueue_if_not_inflight` instead — the unlocked read
        here races with :meth:`get_or_start_inflight`.
        """
        task = self._inflight_enrichment.get(tconst)
        return task is not None and not task.done()

    def get_inflight(self, tconst: str) -> asyncio.Task | None:
        """Return the pending in-flight task for ``tconst`` if one exists.

        Best-effort, non-locking. Returns ``None`` if no task is registered
        or the registered task has already completed. Public accessor so
        callers outside the coordinator don't reach into the private map.
        """
        task = self._inflight_enrichment.get(tconst)
        if task is None or task.done():
            return None
        return task

    async def maybe_enqueue_if_not_inflight(
        self,
        tconst: str,
        row: dict[str, Any] | None,
        tmdb_id: int | None = None,
    ) -> bool:
        """Atomic "check in-flight, else enqueue" for the stale path.

        Holds ``_inflight_lock`` across the lookup and the call into
        :meth:`maybe_enqueue`. The lock covers the ARQ enqueue (one Redis
        round-trip) so a concurrent prefetch handler cannot slip a local
        task into ``_inflight_enrichment`` between the check and the
        enqueue decision. This is the guarantee the stale-path comment
        promises: at most one of {local task, ARQ job} is populated for
        ``tconst``.
        """
        async with self._inflight_lock:
            existing = self._inflight_enrichment.get(tconst)
            if existing is not None and not existing.done():
                return False
            return await self.maybe_enqueue(tconst, row, tmdb_id=tmdb_id)

    async def maybe_enqueue(
        self,
        tconst: str,
        row: dict[str, Any] | None,
        tmdb_id: int | None = None,
    ) -> bool:
        """Try to enqueue background enrichment."""
        now = utcnow()
        last_attempt_at = row.get("last_attempt_at") if row else None
        if last_attempt_at and now < last_attempt_at + ENQUEUE_COOLDOWN:
            return False

        if self.enqueue_fn:
            try:
                result = await self.enqueue_fn(
                    "enrich_projection",
                    tconst,
                    tmdb_id,
                    _job_id=f"enrich:{tconst}",
                )
                if result is not None:
                    await self.store._mark_attempt(tconst, now)
                    return True
            except Exception as exc:
                logger.debug("Failed to enqueue enrich_projection(%s): %s", tconst, exc)

        scheduled = await self._schedule_local_enrichment(tconst, tmdb_id=tmdb_id)
        if scheduled:
            await self.store._mark_attempt(tconst, now)
        return scheduled

    async def _schedule_local_enrichment(
        self,
        tconst: str,
        tmdb_id: int | None = None,
    ) -> bool:
        if not self.tmdb_helper or tconst in self._local_enrichment_tconsts:
            return False

        if len(self._local_enrichment_tconsts) >= self._local_enrichment_max_pending:
            logger.warning(
                "Local enrichment backlog full (%d), dropping schedule for %s",
                len(self._local_enrichment_tconsts),
                tconst,
            )
            _safe_inc(enrichment_backlog_drop_total)
            return False

        self._local_enrichment_tconsts.add(tconst)

        async def _run() -> None:
            try:
                async with self._local_enrichment_semaphore:
                    await self.enrich_projection(tconst, known_tmdb_id=tmdb_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Local enrichment failed for %s: %s", tconst, exc)
            finally:
                self._local_enrichment_tconsts.discard(tconst)

        task = asyncio.create_task(_run())
        self._local_enrichment_tasks.add(task)
        task.add_done_callback(self._local_enrichment_tasks.discard)
        return True

    async def enrich_projection(
        self,
        tconst: str,
        known_tmdb_id: int | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow()
        row = await self.store._select_row(tconst)
        attempts = int(row.get("attempt_count", 0)) + 1 if row else 1
        tmdb_id = known_tmdb_id if known_tmdb_id is not None else (row or {}).get("tmdb_id")
        try:
            movie = Movie(tconst, self.store.db_pool, tmdb_helper=self.tmdb_helper)
            try:
                payload = await asyncio.wait_for(
                    movie.get_movie_data(known_tmdb_id=tmdb_id),
                    timeout=self.ENRICHMENT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                _safe_inc(enrichment_timeout_total)
                raise RuntimeError(
                    "enrichment timeout after %ss" % self.ENRICHMENT_TIMEOUT_SECONDS
                )
            if not payload:
                raise RuntimeError("TMDB enrichment returned no payload")

            payload["tmdb_id"] = payload.get("tmdb_id") or tmdb_id
            payload["projection_state"] = ProjectionState.READY.value
            await self.store._upsert_ready(tconst, payload, now, attempts)
            return payload
        except Exception as exc:
            core_payload = await self.store.ensure_core_projection(tconst)
            await self.store._upsert_failed(
                tconst,
                core_payload or {},
                now,
                attempts,
                str(exc),
                tmdb_id=tmdb_id,
            )
            logger.warning("Projection enrichment failed for %s: %s", tconst, exc)
            return core_payload

    async def drain_pending(self, timeout: float = 5.0) -> None:
        tasks = list(self._local_enrichment_tasks)
        if not tasks:
            return

        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Timed out draining %d local enrichment task(s)", len(tasks))
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self, timeout: float = 5.0) -> None:
        await self.drain_pending(timeout=timeout)
