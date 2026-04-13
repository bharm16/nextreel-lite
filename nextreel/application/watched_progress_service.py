from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import MutableMapping


_IMPORT_TCONSTS_KEY = "letterboxd_import_tconsts"
_SENT_TCONSTS_KEY = "letterboxd_sent_tconsts"
_PENDING_KEY = "letterboxd_enrichment_pending"


@dataclass(slots=True)
class WatchedEnrichmentProgress:
    new_movies: list[dict] = field(default_factory=list)
    new_count: int = 0
    total_ready: int = 0
    total: int = 0
    done: bool = True


class WatchedEnrichmentProgressService:
    async def progress(
        self,
        *,
        session_state: MutableMapping,
        user_id: str,
        watched_store,
        presenter,
        now: datetime,
    ) -> WatchedEnrichmentProgress:
        import_tconsts = list(session_state.get(_IMPORT_TCONSTS_KEY, []))
        if not import_tconsts:
            return WatchedEnrichmentProgress()

        sent_tconsts = set(session_state.get(_SENT_TCONSTS_KEY, []))
        unsent = [tconst for tconst in import_tconsts if tconst not in sent_tconsts]
        if not unsent:
            self._clear_progress(session_state)
            return WatchedEnrichmentProgress(
                total_ready=len(sent_tconsts),
                total=len(import_tconsts),
                done=True,
            )

        newly_ready = await watched_store.ready_tconsts_for_import(unsent)
        if not newly_ready:
            return WatchedEnrichmentProgress(
                total_ready=len(sent_tconsts),
                total=len(import_tconsts),
                done=False,
            )

        newly_ready_list = sorted(newly_ready)
        rows = await watched_store.ready_import_rows(user_id, newly_ready_list)
        movies = [movie for row in rows for movie in [presenter.normalize_movie(row, now)] if movie]

        new_sent = sent_tconsts | set(newly_ready)
        total_ready = len(new_sent)
        total = len(import_tconsts)
        done = total_ready >= total
        if done:
            self._clear_progress(session_state)
        else:
            session_state[_SENT_TCONSTS_KEY] = list(new_sent)

        return WatchedEnrichmentProgress(
            new_movies=movies,
            new_count=len(newly_ready),
            total_ready=total_ready,
            total=total,
            done=done,
        )

    @staticmethod
    def _clear_progress(session_state: MutableMapping) -> None:
        session_state.pop(_PENDING_KEY, None)
        session_state.pop(_IMPORT_TCONSTS_KEY, None)
        session_state.pop(_SENT_TCONSTS_KEY, None)
