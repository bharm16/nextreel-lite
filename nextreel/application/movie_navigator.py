"""Navigation logic backed by MySQL state instead of Quart sessions."""

from __future__ import annotations

from dataclasses import dataclass

from nextreel.domain.filter_contracts import FilterState
from nextreel.domain.navigation_state import (
    FUTURE_STACK_MAX,
    PREV_STACK_MAX,
    QUEUE_REFILL_THRESHOLD,
    QUEUE_TARGET,
    SEEN_MAX,
)
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NavigationOutcome:
    tconst: str | None
    state_conflict: bool = False
    public_id: str | None = None
    title: str | None = None
    year: str | None = None

    @classmethod
    def from_ref(
        cls, ref: dict | None, *, state_conflict: bool = False
    ) -> "NavigationOutcome":
        """Build a NavigationOutcome from a navigator ref dict.

        Carries forward ``public_id``, ``title`` and ``year`` so the
        redirect helper can build the canonical URL without an extra DB
        lookup. ``year`` is coerced to a string for URL-builder
        compatibility.
        """
        if not ref:
            return cls(tconst=None, state_conflict=state_conflict)
        year = ref.get("year")
        return cls(
            tconst=ref.get("tconst"),
            state_conflict=state_conflict,
            public_id=ref.get("public_id"),
            title=ref.get("title"),
            year=str(year) if year is not None else None,
        )


def _movie_ref(movie_data: dict) -> dict:
    """Extract the lightweight reference stored in navigation state.

    The ``imdb_id`` fallback handles dicts produced by ``payload_factory``
    paths (``ProjectionRepository.build_core_payload`` sets both ``tconst``
    and ``imdb_id`` to the same value). It's a synonym, not a separate
    identifier — both keys carry the IMDb ``ttNNNNNNN`` string.
    """
    year = movie_data.get("year") or movie_data.get("startYear")
    return {
        "tconst": movie_data.get("tconst") or movie_data.get("imdb_id"),
        "title": movie_data.get("title") or movie_data.get("primaryTitle"),
        "slug": movie_data.get("slug"),
        "public_id": movie_data.get("public_id"),
        "year": str(year) if year is not None else None,
    }


class MovieNavigator:
    """State-aware next/previous/filter navigation."""

    def __init__(
        self,
        candidate_store,
        navigation_state_store,
        watched_store=None,
        watchlist_store=None,
    ):
        self.candidate_store = candidate_store
        self.navigation_state_store = navigation_state_store
        self.watched_store = watched_store
        self.watchlist_store = watchlist_store

    def prev_stack_length(self, state) -> int:
        return len(state.prev) if state else 0

    def get_current_movie_tconst(self, state) -> str | None:
        return state.current_tconst if state else None

    @staticmethod
    def _set_current(state, ref: dict | None) -> None:
        if not ref or not ref.get("tconst"):
            state.current_tconst = None
            state.current_ref = None
            return
        state.current_tconst = ref["tconst"]
        state.current_ref = _movie_ref(ref)

    def _excluded_tconsts(self, state) -> set[str]:
        excluded = {ref["tconst"] for ref in state.queue if ref.get("tconst")}
        excluded.update({ref["tconst"] for ref in state.prev if ref.get("tconst")})
        excluded.update({ref["tconst"] for ref in state.future if ref.get("tconst")})
        excluded.update(tconst for tconst in state.seen if tconst)
        if state.current_tconst:
            excluded.add(state.current_tconst)
        return excluded

    async def _ref_for_current(self, state) -> dict | None:
        if not state.current_tconst:
            return None
        if getattr(state, "current_ref", None):
            return _movie_ref(state.current_ref)
        ref = await self.candidate_store.fetch_ref(state.current_tconst)
        if ref:
            return ref
        return {"tconst": state.current_tconst, "title": None, "slug": None}

    async def _refill_queue(
        self,
        state,
        desired_size: int,
        *,
        watched_exclusion: set[str] | None = None,
        watchlist_exclusion: set[str] | None = None,
    ) -> None:
        missing = max(0, desired_size - len(state.queue))
        if missing <= 0:
            return

        excluded = self._excluded_tconsts(state)

        if watched_exclusion is None:
            watched_exclusion = await self._watched_exclusion_set(state)
        if watchlist_exclusion is None:
            watchlist_exclusion = await self._watchlist_exclusion_set(state)
        excluded |= watched_exclusion | watchlist_exclusion

        refs = await self.candidate_store.fetch_candidate_refs(
            state.filters,
            excluded,
            missing,
        )
        if refs:
            state.queue.extend(refs)
            state.queue = state.queue[:QUEUE_TARGET]

    async def _watched_exclusion_set(self, state) -> set[str]:
        if (
            not self.watched_store
            or not getattr(state, "user_id", None)
            or not state.filters.get("exclude_watched", True)
        ):
            return set()
        return set(await self.watched_store.watched_tconsts(state.user_id))

    async def _watchlist_exclusion_set(self, state) -> set[str]:
        if (
            not self.watchlist_store
            or not getattr(state, "user_id", None)
            or not state.filters.get("exclude_watchlist", True)
        ):
            return set()
        return set(await self.watchlist_store.watchlist_tconsts(state.user_id))

    async def _pop_next_queue_ref(
        self,
        state,
        watched_exclusion: set[str] | None = None,
        watchlist_exclusion: set[str] | None = None,
    ) -> dict | None:
        if watched_exclusion is None:
            watched_exclusion = await self._watched_exclusion_set(state)
        if watchlist_exclusion is None:
            watchlist_exclusion = await self._watchlist_exclusion_set(state)
        combined = watched_exclusion | watchlist_exclusion
        while state.queue:
            next_ref = state.queue.pop(0)
            tconst = next_ref.get("tconst")
            if tconst and tconst in combined:
                continue
            return next_ref
        return None

    def _mark_seen(self, state, tconst: str | None) -> None:
        if not tconst or tconst in state.seen:
            return
        state.seen.append(tconst)
        state.seen = state.seen[-SEEN_MAX:]

    def _conflict_outcome(self, state) -> NavigationOutcome:
        ref = getattr(state, "current_ref", None) if state else None
        if ref:
            return NavigationOutcome.from_ref(ref, state_conflict=True)
        return NavigationOutcome(
            tconst=state.current_tconst if state else None,
            state_conflict=True,
        )

    async def prewarm_queue(self, session_id: str, legacy_session=None, current_state=None):
        async def mutate(state):
            if not state.queue:
                await self._refill_queue(state, QUEUE_TARGET)
            return len(state.queue)

        result = await self.navigation_state_store.mutate(
            session_id,
            mutate,
            legacy_session=legacy_session,
            current_state=current_state,
        )
        return result.state

    async def next_movie(
        self,
        session_id: str,
        legacy_session=None,
        current_state=None,
    ) -> NavigationOutcome | None:
        async def mutate(state):
            prefilled_empty_queue = False
            next_ref = None
            watched_exclusion = None
            watchlist_exclusion = None
            if state.future:
                next_ref = state.future.pop()
            else:
                watched_exclusion = await self._watched_exclusion_set(state)
                watchlist_exclusion = await self._watchlist_exclusion_set(state)
                if not state.queue:
                    await self._refill_queue(
                        state,
                        QUEUE_TARGET,
                        watched_exclusion=watched_exclusion,
                        watchlist_exclusion=watchlist_exclusion,
                    )
                    prefilled_empty_queue = True
                if state.queue:
                    next_ref = await self._pop_next_queue_ref(
                        state, watched_exclusion, watchlist_exclusion
                    )
                if not next_ref and not prefilled_empty_queue:
                    await self._refill_queue(
                        state,
                        QUEUE_TARGET,
                        watched_exclusion=watched_exclusion,
                        watchlist_exclusion=watchlist_exclusion,
                    )
                    next_ref = await self._pop_next_queue_ref(
                        state, watched_exclusion, watchlist_exclusion
                    )

            if not next_ref or not next_ref.get("tconst"):
                return None

            previous_ref = await self._ref_for_current(state)
            if previous_ref and previous_ref.get("tconst") != next_ref.get("tconst"):
                state.prev.append(previous_ref)
                state.prev = state.prev[-PREV_STACK_MAX:]

            self._set_current(state, next_ref)
            self._mark_seen(state, state.current_tconst)

            if not prefilled_empty_queue and len(state.queue) < QUEUE_REFILL_THRESHOLD:
                await self._refill_queue(
                    state,
                    QUEUE_TARGET,
                    watched_exclusion=watched_exclusion,
                    watchlist_exclusion=watchlist_exclusion,
                )

            return state.current_ref

        result = await self.navigation_state_store.mutate(
            session_id,
            mutate,
            legacy_session=legacy_session,
            current_state=current_state,
        )
        if result.conflicted:
            return self._conflict_outcome(result.state)
        if result.result:
            logger.info("Navigating to next movie %s", result.result.get("tconst"))
            return NavigationOutcome.from_ref(result.result)
        return None

    async def previous_movie(
        self,
        session_id: str,
        legacy_session=None,
        current_state=None,
    ) -> NavigationOutcome | None:
        async def mutate(state):
            if not state.prev:
                return None

            current_ref = await self._ref_for_current(state)
            if current_ref and current_ref.get("tconst"):
                state.future.append(current_ref)
                state.future = state.future[-FUTURE_STACK_MAX:]

            previous_ref = state.prev.pop()
            self._set_current(state, previous_ref)
            return state.current_ref

        result = await self.navigation_state_store.mutate(
            session_id,
            mutate,
            legacy_session=legacy_session,
            current_state=current_state,
        )
        if result.conflicted:
            return self._conflict_outcome(result.state)
        if result.result:
            logger.info("Navigating to previous movie %s", result.result.get("tconst"))
            return NavigationOutcome.from_ref(result.result)
        return None

    async def apply_filters(
        self,
        session_id: str,
        filters: FilterState,
        legacy_session=None,
        current_state=None,
    ) -> NavigationOutcome | None:
        async def mutate(state):
            state.filters = filters
            state.queue = []
            state.prev = []
            state.future = []
            state.seen = []
            self._set_current(state, None)

            await self._refill_queue(state, QUEUE_TARGET)
            if not state.queue:
                return None

            next_ref = state.queue.pop(0)
            self._set_current(state, next_ref)
            self._mark_seen(state, state.current_tconst)
            if len(state.queue) < QUEUE_REFILL_THRESHOLD:
                await self._refill_queue(state, QUEUE_TARGET)
            return state.current_ref

        result = await self.navigation_state_store.mutate(
            session_id,
            mutate,
            legacy_session=legacy_session,
            current_state=current_state,
        )
        if result.conflicted:
            return self._conflict_outcome(result.state)
        if result.result:
            return NavigationOutcome.from_ref(result.result)
        return None
