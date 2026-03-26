"""Navigation logic backed by MySQL state instead of Quart sessions."""

from __future__ import annotations

from quart import redirect, url_for

from infra.navigation_state import (
    FUTURE_STACK_MAX,
    PREV_STACK_MAX,
    QUEUE_REFILL_THRESHOLD,
    QUEUE_TARGET,
    SEEN_MAX,
)
from logging_config import get_logger

logger = get_logger(__name__)


def _movie_ref(movie_data: dict) -> dict:
    """Extract the lightweight reference stored in navigation state."""
    return {
        "tconst": movie_data.get("tconst") or movie_data.get("imdb_id"),
        "title": movie_data.get("title"),
        "slug": movie_data.get("slug"),
    }


class MovieNavigator:
    """State-aware next/previous/filter navigation."""

    def __init__(self, candidate_store, navigation_state_store):
        self.candidate_store = candidate_store
        self.navigation_state_store = navigation_state_store

    def prev_stack_length(self, state) -> int:
        return len(state.prev) if state else 0

    def get_current_movie_tconst(self, state) -> str | None:
        return state.current_tconst if state else None

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
        ref = await self.candidate_store.fetch_ref(state.current_tconst)
        if ref:
            return ref
        return {"tconst": state.current_tconst, "title": None, "slug": None}

    async def _refill_queue(self, state, desired_size: int) -> None:
        missing = max(0, desired_size - len(state.queue))
        if missing <= 0:
            return

        refs = await self.candidate_store.fetch_candidate_refs(
            state.filters,
            self._excluded_tconsts(state),
            missing,
        )
        if refs:
            state.queue.extend(refs)
            state.queue = state.queue[:QUEUE_TARGET]

    def _mark_seen(self, state, tconst: str | None) -> None:
        if not tconst or tconst in state.seen:
            return
        state.seen.append(tconst)
        state.seen = state.seen[-SEEN_MAX:]

    def _conflict_redirect(self, state):
        if state and state.current_tconst:
            return redirect(
                url_for("main.movie_detail", tconst=state.current_tconst, state_conflict=1),
                code=303,
            )
        return redirect(url_for("main.home", state_conflict=1), code=303)

    async def prewarm_queue(self, session_id: str, legacy_session=None):
        async def mutate(state):
            if not state.queue:
                await self._refill_queue(state, QUEUE_TARGET)
            return len(state.queue)

        result = await self.navigation_state_store.mutate(
            session_id,
            mutate,
            legacy_session=legacy_session,
        )
        return result.state

    async def next_movie(self, session_id: str, legacy_session=None):
        """Return ``(response, updated_state)`` tuple."""

        async def mutate(state):
            next_ref = None
            if state.future:
                next_ref = state.future.pop()
            else:
                if len(state.queue) < QUEUE_REFILL_THRESHOLD:
                    await self._refill_queue(state, QUEUE_TARGET)
                if state.queue:
                    next_ref = state.queue.pop(0)

            if not next_ref:
                await self._refill_queue(state, QUEUE_TARGET)
                if state.queue:
                    next_ref = state.queue.pop(0)

            if not next_ref or not next_ref.get("tconst"):
                return None

            previous_ref = await self._ref_for_current(state)
            if previous_ref and previous_ref.get("tconst") != next_ref.get("tconst"):
                state.prev.append(previous_ref)
                state.prev = state.prev[-PREV_STACK_MAX:]

            state.current_tconst = next_ref["tconst"]
            self._mark_seen(state, state.current_tconst)

            if len(state.queue) < QUEUE_REFILL_THRESHOLD:
                await self._refill_queue(state, QUEUE_TARGET)

            return state.current_tconst

        result = await self.navigation_state_store.mutate(
            session_id,
            mutate,
            legacy_session=legacy_session,
        )
        if result.conflicted:
            return self._conflict_redirect(result.state), result.state
        if result.result:
            logger.info("Navigating to next movie %s", result.result)
            return redirect(url_for("main.movie_detail", tconst=result.result), code=303), result.state
        return None, result.state

    async def previous_movie(self, session_id: str, legacy_session=None):
        """Return ``(response, updated_state)`` tuple."""

        async def mutate(state):
            if not state.prev:
                return None

            current_ref = await self._ref_for_current(state)
            if current_ref and current_ref.get("tconst"):
                state.future.append(current_ref)
                state.future = state.future[-FUTURE_STACK_MAX:]

            previous_ref = state.prev.pop()
            state.current_tconst = previous_ref.get("tconst")
            return state.current_tconst

        result = await self.navigation_state_store.mutate(
            session_id,
            mutate,
            legacy_session=legacy_session,
        )
        if result.conflicted:
            return self._conflict_redirect(result.state), result.state
        if result.result:
            logger.info("Navigating to previous movie %s", result.result)
            return redirect(url_for("main.movie_detail", tconst=result.result), code=303), result.state
        return None, result.state

    async def apply_filters(self, session_id: str, filters: dict, legacy_session=None):
        """Return ``(response, updated_state)`` tuple."""

        async def mutate(state):
            state.filters = filters
            state.queue = []
            state.prev = []
            state.future = []
            state.seen = []
            state.current_tconst = None

            await self._refill_queue(state, QUEUE_TARGET)
            if not state.queue:
                return None

            next_ref = state.queue.pop(0)
            state.current_tconst = next_ref.get("tconst")
            self._mark_seen(state, state.current_tconst)
            if len(state.queue) < QUEUE_REFILL_THRESHOLD:
                await self._refill_queue(state, QUEUE_TARGET)
            return state.current_tconst

        result = await self.navigation_state_store.mutate(
            session_id,
            mutate,
            legacy_session=legacy_session,
        )
        if result.conflicted:
            return self._conflict_redirect(result.state), result.state
        if result.result:
            return redirect(url_for("main.movie_detail", tconst=result.result), code=303), result.state
        return None, result.state
