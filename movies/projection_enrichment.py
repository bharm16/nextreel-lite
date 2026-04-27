"""Projection enrichment orchestration and local task lifecycle.

Owns the full enrichment pipeline:
- :class:`ProjectionPayloadDiffer` — detects whether a TMDb-fetched payload
  differs from what is already persisted (lets us skip a full UPSERT).
- :class:`ProjectionEnrichmentService` — fetches TMDb data for a single
  tconst and persists the result through the store.
- :class:`ProjectionEnrichmentCoordinator` — schedules, dedupes, and caps
  concurrency for enrichment work across in-process tasks, ARQ worker
  jobs, and cross-worker Redis locks.

These three were previously in separate files (``projection_enrichment.py``
and ``projection_enrichment_service.py``). They are coupled — the service is
only ever instantiated by the coordinator — so they live together.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from infra.cache import CacheNamespace
from infra.metrics import (
    enrichment_backlog_drop_total,
    enrichment_timeout_total,
)
from infra.metrics_groups import safe_emit
from infra.time_utils import env_int, utcnow
from logging_config import get_logger
from movies.movie import Movie
from movies.projection_repository import _dumps as _json_dumps
from movies.projection_state import (
    ENQUEUE_COOLDOWN,
    EnrichmentResult,
    ProjectionState,
)

# Global enrichment dedup window. Long enough to cover the 25s
# ENRICHMENT_TIMEOUT_SECONDS + ARQ hand-off + retry cooldown. A crashed
# worker's lock expires naturally rather than blocking enrichment forever.
_GLOBAL_ENRICHMENT_LOCK_TTL_SECONDS = 45

if TYPE_CHECKING:
    from movies.projection_store import ProjectionStore

logger = get_logger(__name__)


class ProjectionPayloadDiffer:
    """Compare a freshly-fetched payload to the one already persisted.

    When enrichment returns unchanged data we can skip a full UPSERT and
    just refresh metadata columns (enriched_at, stale_after, etc.). That
    keeps the hot enrichment path cheap during steady-state refreshes.
    """

    def persisted_payload_matches(
        self,
        *,
        store,
        existing,
        payload: dict[str, Any],
    ) -> bool:
        if isinstance(existing, str):
            try:
                existing = json.loads(existing)
            except (TypeError, ValueError):
                existing = None
        if not isinstance(existing, dict):
            return False
        new_persisted = store._persisted_payload(payload)
        new_serialized = _json_dumps(new_persisted, sort_keys=True)
        existing_serialized = _json_dumps(existing, sort_keys=True)
        return new_serialized == existing_serialized


class ProjectionEnrichmentService:
    """Fetch TMDb data for a single tconst and persist the result.

    Encapsulates the wait_for timeout + payload-diff decision so the
    coordinator can stay focused on scheduling and concurrency.
    """

    def __init__(
        self,
        *,
        store,
        tmdb_helper,
        timeout_seconds: float,
        payload_differ: ProjectionPayloadDiffer | None = None,
    ) -> None:
        self.store = store
        self.tmdb_helper = tmdb_helper
        self.timeout_seconds = timeout_seconds
        self.payload_differ = payload_differ or ProjectionPayloadDiffer()

    async def enrich_projection(
        self,
        tconst: str,
        known_tmdb_id: int | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow()
        row = await self.store.select_row(tconst)
        attempts = int(row.get("attempt_count", 0)) + 1 if row else 1
        tmdb_id = known_tmdb_id if known_tmdb_id is not None else (row or {}).get("tmdb_id")
        try:
            movie = Movie(tconst, self.store.db_pool, tmdb_helper=self.tmdb_helper)
            try:
                payload = await asyncio.wait_for(
                    movie.get_movie_data(known_tmdb_id=tmdb_id),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                safe_emit(enrichment_timeout_total.inc)
                raise RuntimeError("enrichment timeout after %ss" % self.timeout_seconds)
            if not payload:
                raise RuntimeError("TMDB enrichment returned no payload")

            payload["tmdb_id"] = payload.get("tmdb_id") or tmdb_id
            payload["projection_state"] = ProjectionState.READY.value

            if row and self.payload_differ.persisted_payload_matches(
                store=self.store,
                existing=row.get("payload_json"),
                payload=payload,
            ):
                logger.debug("payload unchanged for %s, refreshing metadata only", tconst)
                await self.store.apply_enrichment_result(
                    tconst,
                    EnrichmentResult(
                        status="ready",
                        persistence_mode="READY_METADATA_ONLY",
                        payload=payload,
                        attempts=attempts,
                        tmdb_id=payload.get("tmdb_id"),
                        error=None,
                        timestamp=now,
                    ),
                )
                return payload

            await self.store.apply_enrichment_result(
                tconst,
                EnrichmentResult(
                    status="ready",
                    persistence_mode="READY_UPSERT",
                    payload=payload,
                    attempts=attempts,
                    tmdb_id=payload.get("tmdb_id"),
                    error=None,
                    timestamp=now,
                ),
            )
            return payload
        except Exception as exc:
            core_payload = await self.store.ensure_core_projection(tconst)
            await self.store.apply_enrichment_result(
                tconst,
                EnrichmentResult(
                    status="failed",
                    persistence_mode="FAILED_UPSERT",
                    payload=core_payload or {},
                    attempts=attempts,
                    tmdb_id=tmdb_id,
                    error=str(exc),
                    timestamp=now,
                ),
            )
            logger.warning("Projection enrichment failed for %s: %s", tconst, exc)
            return core_payload


class ProjectionEnrichmentCoordinator:
    """Owns enrichment orchestration outside the persistence layer."""

    # Cap concurrent in-process enrichment coroutines so a burst of cache
    # misses (e.g. a filter change touching many stale projections) cannot
    # exhaust the DB pool or trip the TMDb circuit breaker. Overridable via
    # LOCAL_ENRICHMENT_CONCURRENCY env var for load tests.
    LOCAL_ENRICHMENT_CONCURRENCY = env_int("LOCAL_ENRICHMENT_CONCURRENCY", 20)

    # Cap pending in-process enrichment task creation so a burst can't
    # accumulate hundreds of coroutines waiting on the semaphore. Overridable
    # via LOCAL_ENRICHMENT_MAX_PENDING env var.
    LOCAL_ENRICHMENT_MAX_PENDING = env_int("LOCAL_ENRICHMENT_MAX_PENDING", 200)

    # Emit a warning when the backlog crosses this fraction of the cap, so
    # ops can react before tasks start being dropped.
    LOCAL_ENRICHMENT_HIGH_WATERMARK = 0.70

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
        cache=None,
    ) -> None:
        self.store = store
        self.tmdb_helper = tmdb_helper
        self._enrichment_service = ProjectionEnrichmentService(
            store=store,
            tmdb_helper=tmdb_helper,
            timeout_seconds=self.ENRICHMENT_TIMEOUT_SECONDS,
        )
        self.enqueue_fn = enqueue_fn
        # Optional shared Redis cache for cross-worker enrichment dedup.
        # Wired by MovieManager.attach_cache when Redis is available.
        self._cache = cache
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
        # Edge-triggered flag for the high-watermark warning so a burst
        # doesn't produce hundreds of identical log lines. Reset when the
        # backlog drains below 50% of cap.
        self._high_watermark_fired = False
        # App-level background scheduler (wired from app.create_app). Lets
        # prefetch tasks register in app.background_tasks so they survive
        # request teardown and are drained on shutdown.
        self._background_scheduler = background_scheduler

    def attach_cache(self, cache) -> None:
        """Attach a cache manager. Idempotent — callers may rebind."""
        self._cache = cache

    def attach_background_scheduler(self, scheduler) -> None:
        """Wire an app-owned background task scheduler."""
        self._background_scheduler = scheduler

    async def get_or_start_inflight(
        self,
        tconst: str,
        tmdb_id: int | None = None,
    ) -> asyncio.Task | None:
        """Atomically return an existing in-flight enrichment task for
        ``tconst`` or start a new one.

        Returns ``None`` when the in-flight backlog is at capacity so a
        burst of distinct-tconst prefetches (e.g. spam-clicked navigation)
        cannot grow the map without bound. Callers MUST treat ``None`` as
        "not started" and skip awaiting.

        The single coordinator lock is held only across map lookup and task
        creation/insertion — never across the enrichment work itself. A task
        is removed from the map on completion (success or failure) so a
        failed task does not poison later lookups.
        """
        async with self._inflight_lock:
            existing = self._inflight_enrichment.get(tconst)
            if existing is not None and not existing.done():
                return existing

            pending = len(self._inflight_enrichment)
            cap = self._local_enrichment_max_pending
            if pending >= cap:
                logger.warning(
                    "Inflight enrichment backlog full (%d/%d), dropping schedule for %s",
                    pending,
                    cap,
                    tconst,
                )
                safe_emit(enrichment_backlog_drop_total.inc)
                return None

            task = asyncio.create_task(self._run_inflight_enrichment(tconst, tmdb_id))
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
        """Try to enqueue background enrichment.

        Cross-worker dedup: acquires a Redis SET NX lock before enqueueing
        so N workers racing on the same tconst collapse to a single job.
        The lock is fail-open — ARQ's ``_job_id`` dedup still catches
        duplicates if Redis is unavailable.
        """
        now = utcnow()
        last_attempt_at = row.get("last_attempt_at") if row else None
        if last_attempt_at and now < last_attempt_at + ENQUEUE_COOLDOWN:
            return False

        lock_key = f"enrich_inflight:{tconst}"
        lock_held = False
        if self._cache is not None:
            acquired = await self._cache.try_acquire_lock(
                CacheNamespace.TEMP,
                lock_key,
                ttl_seconds=_GLOBAL_ENRICHMENT_LOCK_TTL_SECONDS,
            )
            if not acquired:
                return False
            lock_held = True

        try:
            if self.enqueue_fn:
                try:
                    result = await self.enqueue_fn(
                        "enrich_projection",
                        tconst,
                        tmdb_id,
                        _job_id=f"enrich:{tconst}",
                    )
                    if result is not None:
                        await self.store.mark_attempt(tconst, now)
                        lock_held = False  # ARQ job owns the dedup window now.
                        return True
                except Exception as exc:
                    logger.debug("Failed to enqueue enrich_projection(%s): %s", tconst, exc)

            scheduled = await self._schedule_local_enrichment(tconst, tmdb_id=tmdb_id)
            if scheduled:
                await self.store.mark_attempt(tconst, now)
                lock_held = False  # Local task owns the dedup window now.
            return scheduled
        finally:
            # Release the lock if no downstream path took ownership, so
            # the next retry isn't blocked for the full TTL.
            if lock_held and self._cache is not None:
                await self._cache.release_lock(CacheNamespace.TEMP, lock_key)

    async def _schedule_local_enrichment(
        self,
        tconst: str,
        tmdb_id: int | None = None,
    ) -> bool:
        if not self.tmdb_helper or tconst in self._local_enrichment_tconsts:
            return False

        pending = len(self._local_enrichment_tconsts)
        cap = self._local_enrichment_max_pending
        if pending >= cap:
            logger.warning(
                "Local enrichment backlog full (%d/%d), dropping schedule for %s",
                pending,
                cap,
                tconst,
            )
            safe_emit(enrichment_backlog_drop_total.inc)
            return False

        high_threshold = int(cap * self.LOCAL_ENRICHMENT_HIGH_WATERMARK)
        if pending >= high_threshold and not self._high_watermark_fired:
            self._high_watermark_fired = True
            logger.warning(
                "Local enrichment backlog high-watermark: %d/%d (>=%.0f%%)",
                pending,
                cap,
                self.LOCAL_ENRICHMENT_HIGH_WATERMARK * 100,
            )
        elif pending < cap // 2:
            self._high_watermark_fired = False

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
        self._enrichment_service.tmdb_helper = self.tmdb_helper
        self._enrichment_service.timeout_seconds = self.ENRICHMENT_TIMEOUT_SECONDS
        return await self._enrichment_service.enrich_projection(
            tconst,
            known_tmdb_id=known_tmdb_id,
        )

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
