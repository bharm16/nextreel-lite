# Letterboxd Post-Import Enrichment + Progressive Display

## Summary

After a Letterboxd CSV import adds tconsts to the watched list, batch-enqueue TMDb enrichment for all un-enriched movies. The watched list only displays movies with `READY` projections. A silent JS poller fetches newly-enriched movie cards and appends them to the grid in real time — no banners, no page reloads.

## Context

- The Letterboxd CSV import (completed feature) adds ~1,700 tconsts to `user_watched_movies`. Most lack TMDb enrichment (`movie_projection` rows in `READY` state).
- The existing `enrich_projection` arq job handles per-movie TMDb enrichment with 15-min cooldown, Redis dedup lock (`enrich_inflight:{tconst}`), and arq job-ID dedup (`enrich:{tconst}`).
- The TMDb rate semaphore allows 200 concurrent requests (configurable via `TMDB_RATE_SEMAPHORE`).
- The watched list presenter already builds movie dicts from `movie_candidates` + `movie_projection` data.

## Data Flow

1. Import route calls `add_bulk()` successfully.
2. Route fires `asyncio.create_task(enqueue_import_enrichment(...))` — non-blocking.
3. Route stores `letterboxd_import_tconsts` (list of imported tconsts) and `letterboxd_enrichment_pending = True` in session.
4. Page renders normally — only movies with `READY` projections appear in the grid.
5. Template detects `letterboxd_enrichment_pending` and injects a silent JS poller.
6. JS calls `GET /watched/enrichment-progress` every 10 seconds.
7. Endpoint checks which imported tconsts now have `READY` projections and haven't been sent yet. Returns rendered HTML card fragments.
8. JS appends fragments to the grid.
9. When all tconsts are ready or 10 minutes elapse, polling stops. Session flags cleared by the endpoint returning `done: true`.

## Components

### 1. Batch Enqueue — `movies/letterboxd_import.py`

New async function:

```
async def enqueue_import_enrichment(
    tconsts: list[str],
    db_pool,
    enqueue_fn,
) -> None
```

- Queries `movie_projection` to find which tconsts are missing or not in `READY` state.
- Enqueues `enrich_projection` jobs for those tconsts via `enqueue_fn`.
- Uses existing arq dedup (`_job_id=f"enrich:{tconst}"`) — safe to call even if jobs already exist.
- Enqueues in batches of 50 with a 1-second `asyncio.sleep` between batches to avoid flooding the arq queue.
- Catches and logs exceptions — this runs in a fire-and-forget task, must not crash.

### 2. Card Partial — `templates/_watched_card.html`

Extract the existing card markup from `watched_list.html` (lines 97-131) into a reusable Jinja partial. The full page template and the progress endpoint both render this partial.

The partial receives a single `movie` dict with keys: `tconst`, `title`, `year`, `poster_url`, `tmdb_rating`, `watched_at`, `slug`.

### 3. Progress Endpoint — `GET /watched/enrichment-progress`

New route in `nextreel/web/routes/watched.py`:

- Login-required (no CSRF needed — GET request).
- Reads `letterboxd_import_tconsts` and `letterboxd_sent_tconsts` from session.
- Queries `movie_projection` for tconsts that are now `READY` and not in `letterboxd_sent_tconsts`.
- For newly-ready tconsts, fetches full movie data (same presenter logic as `list_watched`) and renders `_watched_card.html` for each.
- Adds newly-sent tconsts to `letterboxd_sent_tconsts` in session.
- Returns JSON: `{"html": "<rendered cards>", "new_count": N, "total_ready": M, "total": T, "done": bool}`.
- When `done` is true (all tconsts ready, or no import tconsts in session), clears session flags.

### 4. Template Changes — `templates/watched_list.html`

- Replace inline card markup with `{% include '_watched_card.html' %}`.
- When `session.get('letterboxd_enrichment_pending')`, inject a `<script>` block that:
  - Polls `/watched/enrichment-progress` every 10 seconds via `fetch()`.
  - Parses JSON response, appends `html` to the `#watched-grid` element.
  - Updates the visible count display.
  - Stops polling when `done` is true or 10 minutes have elapsed.
  - No visible UI — cards simply appear.

### 5. Import Route Changes — `nextreel/web/routes/watched.py`

After `add_bulk()` succeeds:
- Set `session['letterboxd_import_tconsts'] = result.matched` (list of matched tconst strings).
- Set `session['letterboxd_enrichment_pending'] = True`.
- Set `session['letterboxd_sent_tconsts'] = []`.
- Fire `asyncio.create_task(enqueue_import_enrichment(result.matched, db_pool, enqueue_fn))`.
- Existing flash message and redirect unchanged.

### 6. Watched List Filtering

The `list_watched` query in `watched_store.py` currently returns all watched movies regardless of projection state. To only show enriched movies during an active import:

- The `watched_list_page` route checks `session.get('letterboxd_enrichment_pending')`.
- If true, adds a filter to the query: `INNER JOIN movie_projection p ON w.tconst = p.tconst` (instead of `LEFT JOIN`) with `WHERE p.state = 'ready'`.
- If false (normal case), behavior unchanged — uses existing LEFT JOIN.

This means: during an active enrichment, only enriched movies display. Once enrichment completes (session flag cleared), all movies display (existing behavior with LEFT JOIN).

## Error Handling

| Condition | Behavior |
|---|---|
| `enqueue_fn` is None (no arq worker) | Log warning, skip enrichment silently. Movies show as they get enriched on-demand when viewed individually. |
| arq queue full or Redis down | `enqueue_import_enrichment` catches exceptions, logs them. Partial enrichment still occurs. |
| `create_task` exception | Caught by asyncio — logged. Import itself already succeeded. |
| Progress endpoint called with no session flags | Returns `{"html": "", "new_count": 0, "total_ready": 0, "total": 0, "done": true}`. |
| TMDb rate limit during enrichment | Existing circuit breaker + retry logic in `tmdb_client.py` handles this. |

## Not Included

- No banner or spinner — cards appear silently.
- No WebSocket — polling every 10s is sufficient.
- No retry UI for failed enrichments — existing 6-hour retry cooldown handles this.
- No progress percentage — user doesn't see enrichment status.
- No changes to existing enrichment pipeline — reuses `enrich_projection` as-is.

## Testing

- Unit test for `enqueue_import_enrichment()` — verifies it calls `enqueue_fn` for non-ready tconsts only.
- Unit test for progress endpoint — verifies it returns newly-ready movies as HTML, tracks sent tconsts, returns done when complete.
- Integration test: import CSV, verify enrichment jobs enqueued, verify progress endpoint returns cards as enrichment completes.
