"""Read-path policy for projection payload rendering."""

from __future__ import annotations

from infra.time_utils import env_bool, utcnow
from movies.projection_state import ProjectionState


def _enrichment_blocks_render() -> bool:
    return env_bool("PROJECTION_ENRICHMENT_BLOCKS_RENDER", default=False)


class ProjectionReadService:
    def __init__(self, *, repository, coordinator, enrich_projection):
        self.repository = repository
        self.coordinator = coordinator
        self._enrich_projection = enrich_projection

    async def fetch_renderable_payload(self, tconst: str):
        inflight = self.coordinator.get_inflight(tconst) if self.coordinator else None
        if inflight is not None:
            try:
                enriched = await inflight
            except Exception:
                enriched = None
            if enriched:
                return enriched

        row = await self.repository.select_row(tconst)
        now = utcnow()
        if row:
            state = row["projection_state"]
            stale_after = row.get("stale_after")
            if (
                state == ProjectionState.READY.value
                and stale_after
                and stale_after <= now
            ):
                await self.repository.mark_ready_stale_if_due(tconst)
                row["projection_state"] = ProjectionState.STALE.value
                state = ProjectionState.STALE.value

            if state == ProjectionState.READY.value:
                return self.repository.payload_from_row(row)

            if state == ProjectionState.STALE.value:
                if self.coordinator is not None:
                    await self.coordinator.maybe_enqueue_if_not_inflight(
                        tconst,
                        row,
                        tmdb_id=row.get("tmdb_id"),
                    )
                return self.repository.payload_from_row(row)

            if ProjectionState(state).needs_enrichment():
                if _enrichment_blocks_render():
                    enriched = await self._enrich_projection(
                        tconst,
                        known_tmdb_id=row.get("tmdb_id"),
                    )
                    if enriched:
                        return enriched
                else:
                    if self.coordinator is not None:
                        await self.coordinator.maybe_enqueue_if_not_inflight(
                            tconst,
                            row,
                            tmdb_id=row.get("tmdb_id"),
                        )

                payload = self.repository.payload_from_row(row)
                if not payload or payload.get("projection_state") == ProjectionState.FAILED.value:
                    payload = await self.repository.ensure_core_projection(tconst)
                return payload

        if _enrichment_blocks_render():
            enriched = await self._enrich_projection(tconst)
            if enriched:
                return enriched

        payload = await self.repository.ensure_core_projection(tconst)
        if not _enrichment_blocks_render() and self.coordinator is not None and payload:
            await self.coordinator.maybe_enqueue_if_not_inflight(
                tconst,
                None,
                tmdb_id=None,
            )
        return payload
