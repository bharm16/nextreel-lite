# Letterboxd Post-Import Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a Letterboxd CSV import, batch-enqueue TMDb enrichment for un-enriched movies and silently append newly-enriched movie cards to the watched list via JS polling.

**Architecture:** New `enqueue_import_enrichment()` in `movies/letterboxd_import.py` batch-enqueues arq jobs. A new `GET /watched/enrichment-progress` endpoint returns server-rendered HTML card fragments for newly-ready movies. The watched list template includes a silent JS poller when enrichment is pending. The watched list page filters to only show enriched movies during an active import.

**Tech Stack:** Existing arq worker + `enrich_projection` job, Quart session for state tracking, Jinja partial for card rendering, vanilla JS `fetch()` polling.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `movies/letterboxd_import.py` | Modify (add function) | `enqueue_import_enrichment()` |
| `templates/_watched_card.html` | Create | Reusable card partial |
| `templates/watched_list.html` | Modify | Use partial, add polling JS, filter enriched-only |
| `nextreel/web/routes/watched.py` | Modify | Add `create_task` to import route, add progress endpoint |
| `tests/movies/test_letterboxd_import.py` | Modify | Tests for `enqueue_import_enrichment` |

---

### Task 1: Extract Card Partial

**Files:**
- Create: `templates/_watched_card.html`
- Modify: `templates/watched_list.html:183-219`

- [ ] **Step 1: Create the card partial**

Create `templates/_watched_card.html` — extract the card markup from `watched_list.html` lines 185-218:

```html
{% set href = url_for('main.movie_detail', tconst=movie.tconst) %}
<div
  class="watched-card"
  data-tconst="{{ movie.tconst }}"
  data-title="{{ movie.title }}"
  data-year="{{ movie.year if movie.year else '' }}"
  data-rating="{{ movie.tmdb_rating }}"
  data-watched="{{ movie.watched_at }}"
  data-search="{{ movie.title|lower }} {{ movie.year or '' }}"
>
  <a class="watched-poster-link" href="{{ href }}">
    <img
      class="watched-poster"
      src="{{ movie.poster_url }}"
      alt="{{ movie.title }}{% if movie.year %} ({{ movie.year }}){% endif %} poster"
      loading="lazy"
    />
    <div class="watched-card-overlay" aria-hidden="true">
      <div class="watched-card-meta">
        <div class="watched-card-title">{{ movie.title }}</div>
        {% if movie.year %}<div class="watched-card-year">{{ movie.year }}</div>{% endif %}
      </div>
    </div>
  </a>
  <button
    type="button"
    class="watched-remove"
    data-tconst="{{ movie.tconst }}"
    aria-label="Remove {{ movie.title }} from watched"
  >
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
      <path d="M18 6L6 18M6 6l12 12"/>
    </svg>
  </button>
</div>
```

- [ ] **Step 2: Update `watched_list.html` to use the partial**

Replace lines 183-219 in `templates/watched_list.html` (the `{% for movie in movies %}` block inside the grid section):

Old:
```html
      <section class="watched-grid auth-fade-in" id="watched-grid">
        {% for movie in movies %}
          {% set href = url_for('main.movie_detail', tconst=movie.tconst) %}
          <div
            class="watched-card"
            ...entire card markup...
          </div>
        {% endfor %}
      </section>
```

New:
```html
      <section class="watched-grid auth-fade-in" id="watched-grid">
        {% for movie in movies %}
          {% include '_watched_card.html' %}
        {% endfor %}
      </section>
```

- [ ] **Step 3: Verify the page still renders**

Run: `python3 app.py` and visit `/watched` in browser. The page should look identical.

- [ ] **Step 4: Commit**

```bash
git add templates/_watched_card.html templates/watched_list.html
git commit -m "refactor: extract watched card into reusable partial"
```

---

### Task 2: Batch Enqueue Function

**Files:**
- Modify: `tests/movies/test_letterboxd_import.py` (add tests)
- Modify: `movies/letterboxd_import.py` (add `enqueue_import_enrichment`)

- [ ] **Step 1: Write failing tests for `enqueue_import_enrichment`**

Append to `tests/movies/test_letterboxd_import.py`:

```python
import asyncio
from movies.letterboxd_import import enqueue_import_enrichment


class TestEnqueueImportEnrichment:
    async def test_enqueues_jobs_for_non_ready_tconsts(self, mock_db_pool):
        """Only tconsts without READY projections get enqueued."""
        # Simulate: tt0000001 is READY, tt0000002 has no projection
        mock_db_pool.execute.return_value = [{"tconst": "tt0000001"}]

        enqueue_fn = AsyncMock()
        await enqueue_import_enrichment(
            ["tt0000001", "tt0000002"], mock_db_pool, enqueue_fn
        )

        # Only tt0000002 should be enqueued (tt0000001 is already READY)
        enqueue_fn.assert_awaited_once()
        call_args = enqueue_fn.call_args
        assert call_args[0][0] == "enrich_projection"
        assert call_args[0][1] == "tt0000002"

    async def test_empty_tconsts_does_nothing(self, mock_db_pool):
        enqueue_fn = AsyncMock()
        await enqueue_import_enrichment([], mock_db_pool, enqueue_fn)
        enqueue_fn.assert_not_awaited()
        mock_db_pool.execute.assert_not_awaited()

    async def test_all_already_ready(self, mock_db_pool):
        """If all tconsts are READY, no jobs enqueued."""
        mock_db_pool.execute.return_value = [
            {"tconst": "tt0000001"},
            {"tconst": "tt0000002"},
        ]
        enqueue_fn = AsyncMock()
        await enqueue_import_enrichment(
            ["tt0000001", "tt0000002"], mock_db_pool, enqueue_fn
        )
        enqueue_fn.assert_not_awaited()

    async def test_enqueue_failure_does_not_raise(self, mock_db_pool):
        """Enqueue errors are caught, not propagated."""
        mock_db_pool.execute.return_value = []
        enqueue_fn = AsyncMock(side_effect=Exception("arq down"))
        # Should not raise
        await enqueue_import_enrichment(
            ["tt0000001"], mock_db_pool, enqueue_fn
        )

    async def test_none_enqueue_fn_skips_silently(self, mock_db_pool):
        """If enqueue_fn is None, skip without error."""
        mock_db_pool.execute.return_value = []
        await enqueue_import_enrichment(
            ["tt0000001"], mock_db_pool, None
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_letterboxd_import.py::TestEnqueueImportEnrichment -v`
Expected: FAIL — `cannot import name 'enqueue_import_enrichment'`

- [ ] **Step 3: Implement `enqueue_import_enrichment`**

Add to the end of `movies/letterboxd_import.py`:

```python
import asyncio as _asyncio

_ENQUEUE_BATCH_SIZE = 50
_ENQUEUE_BATCH_DELAY = 1.0  # seconds between batches


async def enqueue_import_enrichment(
    tconsts: list[str],
    db_pool,
    enqueue_fn,
) -> None:
    """Batch-enqueue enrichment jobs for imported tconsts lacking READY projections.

    Runs as a fire-and-forget task. Catches all exceptions to avoid crashing.

    Args:
        tconsts: list of tconst strings from the import.
        db_pool: database pool for querying projection state.
        enqueue_fn: async callable to enqueue arq jobs, or None to skip.
    """
    if not tconsts or enqueue_fn is None:
        return

    try:
        # Find which tconsts already have READY projections
        placeholders = ", ".join(["%s"] * len(tconsts))
        ready_rows = await db_pool.execute(
            "SELECT tconst FROM movie_projection "
            "WHERE tconst IN (" + placeholders + ") "
            "AND projection_state = %s",
            [*tconsts, "ready"],
            fetch="all",
        )
        ready_set = {row["tconst"] for row in ready_rows} if ready_rows else set()
        needs_enrichment = [tc for tc in tconsts if tc not in ready_set]

        if not needs_enrichment:
            logger.info("All %d imported tconsts already READY, skipping enrichment", len(tconsts))
            return

        logger.info(
            "Enqueuing enrichment for %d of %d imported tconsts",
            len(needs_enrichment),
            len(tconsts),
        )

        for i in range(0, len(needs_enrichment), _ENQUEUE_BATCH_SIZE):
            batch = needs_enrichment[i : i + _ENQUEUE_BATCH_SIZE]
            for tc in batch:
                try:
                    await enqueue_fn(
                        "enrich_projection", tc, None,
                        _job_id="enrich:%s" % tc,
                    )
                except Exception:
                    logger.debug("Failed to enqueue enrichment for %s", tc, exc_info=True)

            if i + _ENQUEUE_BATCH_SIZE < len(needs_enrichment):
                await _asyncio.sleep(_ENQUEUE_BATCH_DELAY)

    except Exception:
        logger.exception("enqueue_import_enrichment failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_letterboxd_import.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add movies/letterboxd_import.py tests/movies/test_letterboxd_import.py
git commit -m "feat: add batch enrichment enqueue for Letterboxd imports"
```

---

### Task 3: Import Route — Fire Enrichment + Set Session Flags

**Files:**
- Modify: `nextreel/web/routes/watched.py:168-201` (modify import_letterboxd)

- [ ] **Step 1: Update the import route**

In `nextreel/web/routes/watched.py`, modify the `import_letterboxd` function. After the `add_bulk` call (line 175) and before the flash message logic (line 182), add session flags and fire the enrichment task.

Replace lines 168-201 (from `try: result = await match_films` to the end of the function) with:

```python
    try:
        result = await match_films(
            services.movie_manager.db_pool,
            films,
        )
        added = await services.movie_manager.watched_store.add_bulk(
            user_id, result.matched
        )
    except Exception:
        logger.exception("Letterboxd import failed for user %s", user_id)
        await flash("Something went wrong during import. Please try again.", "error")
        return redirect(url_for("main.watched_list_page"))

    # Fire non-blocking enrichment for un-enriched movies
    from quart import current_app
    from movies.letterboxd_import import enqueue_import_enrichment

    enqueue_fn = getattr(current_app, "enqueue_runtime_job", None)
    if enqueue_fn and result.matched:
        asyncio.create_task(
            enqueue_import_enrichment(
                result.matched,
                services.movie_manager.db_pool,
                enqueue_fn,
            )
        )
        quart_session["letterboxd_import_tconsts"] = result.matched
        quart_session["letterboxd_enrichment_pending"] = True
        quart_session["letterboxd_sent_tconsts"] = []

    # Build flash message
    matched_count = len(result.matched)
    unmatched_count = len(result.unmatched)
    if unmatched_count:
        await flash(
            "Imported %d films. %d could not be matched." % (matched_count, unmatched_count),
            "success",
        )
        quart_session["letterboxd_unmatched"] = [
            "%s (%s)" % (u["name"], u["year"]) for u in result.unmatched[:50]
        ]
    else:
        await flash("Imported all %d films." % matched_count, "success")

    logger.info(
        "Letterboxd import for user %s: %d matched, %d unmatched",
        user_id,
        matched_count,
        unmatched_count,
    )
    return redirect(url_for("main.watched_list_page"))
```

- [ ] **Step 2: Run tests to verify no regressions**

Run: `python3 -m pytest tests/ -v --timeout=30 2>&1 | tail -20`
Expected: existing tests still pass

- [ ] **Step 3: Commit**

```bash
git add nextreel/web/routes/watched.py
git commit -m "feat: fire enrichment jobs and set session flags after Letterboxd import"
```

---

### Task 4: Watched List — Filter to Enriched-Only During Import

**Files:**
- Modify: `nextreel/web/routes/watched.py:38-70` (modify `watched_list_page`)
- Modify: `movies/watched_store.py` (add `list_watched_enriched` method)

- [ ] **Step 1: Add `list_watched_enriched` to WatchedStore**

Append to `movies/watched_store.py` (after the `list_watched` method):

```python
    async def list_watched_enriched(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return watched movies that have READY projections, ordered by most recently watched."""
        rows = await self.db_pool.execute(
            """
            SELECT w.tconst, w.watched_at,
                   c.primaryTitle, c.startYear, c.genres, c.slug,
                   p.payload_json
            FROM user_watched_movies w
            INNER JOIN movie_projection p ON w.tconst = p.tconst
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            WHERE w.user_id = %s AND p.projection_state = %s
            ORDER BY w.watched_at DESC
            LIMIT %s OFFSET %s
            """,
            [user_id, "ready", limit, offset],
            fetch="all",
        )
        return rows if rows else []

    async def count_enriched(self, user_id: str) -> int:
        """Return count of watched movies with READY projections."""
        row = await self.db_pool.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM user_watched_movies w
            INNER JOIN movie_projection p ON w.tconst = p.tconst
            WHERE w.user_id = %s AND p.projection_state = %s
            """,
            [user_id, "ready"],
            fetch="one",
        )
        return row["cnt"] if row else 0
```

- [ ] **Step 2: Update `watched_list_page` to use enriched-only during import**

Modify the `watched_list_page` function in `nextreel/web/routes/watched.py`. Replace lines 44-54 (the data fetching section):

```python
    user_id = _current_user_id()
    services = _services()

    page, per_page, offset = _parse_watched_pagination(request.args)

    from quart import session as quart_session

    enrichment_pending = quart_session.get("letterboxd_enrichment_pending", False)

    if enrichment_pending:
        raw_rows, total_count = await asyncio.gather(
            services.movie_manager.watched_store.list_watched_enriched(
                user_id, limit=per_page, offset=offset
            ),
            services.movie_manager.watched_store.count_enriched(user_id),
        )
    else:
        raw_rows, total_count = await asyncio.gather(
            services.movie_manager.watched_store.list_watched(
                user_id, limit=per_page, offset=offset
            ),
            services.movie_manager.watched_store.count(user_id),
        )
```

Also pass `enrichment_pending` to the template. Update the `render_template` call:

```python
    return await render_template(
        "watched_list.html",
        movies=view_model.movies,
        stats=view_model.stats,
        total=view_model.total,
        pagination=view_model.pagination,
        enrichment_pending=enrichment_pending,
    )
```

- [ ] **Step 3: Run tests to verify no regressions**

Run: `python3 -m pytest tests/ -v --timeout=30 2>&1 | tail -20`
Expected: existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add movies/watched_store.py nextreel/web/routes/watched.py
git commit -m "feat: filter watched list to enriched-only during active import"
```

---

### Task 5: Progress Endpoint

**Files:**
- Modify: `nextreel/web/routes/watched.py` (add `GET /watched/enrichment-progress`)

- [ ] **Step 1: Add the progress endpoint**

Add after the `import_letterboxd` function in `nextreel/web/routes/watched.py`:

```python
@bp.route("/watched/enrichment-progress")
async def enrichment_progress():
    redirect_response = _require_login()
    if redirect_response:
        return jsonify({"html": "", "new_count": 0, "total_ready": 0, "total": 0, "done": True})

    from quart import session as quart_session

    import_tconsts = quart_session.get("letterboxd_import_tconsts", [])
    if not import_tconsts:
        return jsonify({"html": "", "new_count": 0, "total_ready": 0, "total": 0, "done": True})

    sent_tconsts = set(quart_session.get("letterboxd_sent_tconsts", []))
    services = _services()

    # Find newly READY tconsts we haven't sent yet
    unsent = [tc for tc in import_tconsts if tc not in sent_tconsts]
    if not unsent:
        # All have been sent already — check if we're done
        quart_session.pop("letterboxd_enrichment_pending", None)
        quart_session.pop("letterboxd_import_tconsts", None)
        quart_session.pop("letterboxd_sent_tconsts", None)
        return jsonify({
            "html": "", "new_count": 0,
            "total_ready": len(sent_tconsts), "total": len(import_tconsts),
            "done": True,
        })

    # Query which unsent tconsts are now READY
    placeholders = ", ".join(["%s"] * len(unsent))
    ready_rows = await services.movie_manager.db_pool.execute(
        "SELECT tconst FROM movie_projection "
        "WHERE tconst IN (" + placeholders + ") "
        "AND projection_state = %s",
        [*unsent, "ready"],
        fetch="all",
    )
    newly_ready = {row["tconst"] for row in ready_rows} if ready_rows else set()

    if not newly_ready:
        total_ready = len(sent_tconsts)
        total = len(import_tconsts)
        return jsonify({
            "html": "", "new_count": 0,
            "total_ready": total_ready, "total": total,
            "done": False,
        })

    # Fetch full movie data for newly ready tconsts
    newly_ready_list = sorted(newly_ready)
    placeholders2 = ", ".join(["%s"] * len(newly_ready_list))
    rows = await services.movie_manager.db_pool.execute(
        """
        SELECT w.tconst, w.watched_at,
               c.primaryTitle, c.startYear, c.genres, c.slug,
               p.payload_json
        FROM user_watched_movies w
        INNER JOIN movie_projection p ON w.tconst = p.tconst
        LEFT JOIN movie_candidates c ON w.tconst = c.tconst
        WHERE w.tconst IN (""" + placeholders2 + """)
        AND w.user_id = %s
        AND p.projection_state = %s
        ORDER BY w.watched_at DESC
        """,
        [*newly_ready_list, _current_user_id(), "ready"],
        fetch="all",
    )

    # Build movie dicts using the presenter
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    html_parts = []
    if rows:
        for row in rows:
            movie, _, _ = _watched_list_presenter._normalize_row(row, now)
            if movie:
                html_parts.append(
                    await render_template("_watched_card.html", movie=movie)
                )

    # Update sent tracking
    new_sent = sent_tconsts | newly_ready
    quart_session["letterboxd_sent_tconsts"] = list(new_sent)

    total_ready = len(new_sent)
    total = len(import_tconsts)
    done = total_ready >= total

    if done:
        quart_session.pop("letterboxd_enrichment_pending", None)
        quart_session.pop("letterboxd_import_tconsts", None)
        quart_session.pop("letterboxd_sent_tconsts", None)

    return jsonify({
        "html": "".join(html_parts),
        "new_count": len(newly_ready),
        "total_ready": total_ready,
        "total": total,
        "done": done,
    })
```

- [ ] **Step 2: Add to `__all__`**

Update `__all__`:
```python
__all__ = [
    "add_to_watched",
    "enrichment_progress",
    "import_letterboxd",
    "remove_from_watched",
    "watched_list_page",
]
```

- [ ] **Step 3: Commit**

```bash
git add nextreel/web/routes/watched.py
git commit -m "feat: add GET /watched/enrichment-progress endpoint"
```

---

### Task 6: Silent Polling JS

**Files:**
- Modify: `templates/watched_list.html` (add polling script)

- [ ] **Step 1: Add silent polling JS**

In `templates/watched_list.html`, insert the following script block just before the closing `</body>` tag (before `{% include 'footer_modern.html' %}` on line 355):

```html
  {% if enrichment_pending %}
  <script>
    (function () {
      var grid = document.getElementById('watched-grid');
      if (!grid) return;

      var POLL_INTERVAL = 10000;
      var MAX_POLL_DURATION = 600000; // 10 minutes
      var startTime = Date.now();
      var pollTimer = null;

      function pollProgress() {
        if (Date.now() - startTime > MAX_POLL_DURATION) {
          return; // Stop after 10 minutes
        }

        fetch('/watched/enrichment-progress', {
          credentials: 'same-origin',
          headers: { 'Accept': 'application/json' },
        })
        .then(function (resp) {
          if (!resp.ok) return null;
          return resp.json();
        })
        .then(function (data) {
          if (!data) return;

          if (data.html) {
            var temp = document.createElement('div');
            temp.innerHTML = data.html;
            var cards = temp.querySelectorAll('.watched-card');
            cards.forEach(function (card) {
              grid.appendChild(card);
            });

            // Update the allCards array and count if they exist
            var countEl = document.getElementById('watched-count');
            if (countEl) {
              var currentCards = grid.querySelectorAll('.watched-card');
              countEl.textContent = currentCards.length + ' of ' + currentCards.length;
            }
          }

          if (data.done) {
            return; // Stop polling
          }

          pollTimer = setTimeout(pollProgress, POLL_INTERVAL);
        })
        .catch(function () {
          // Silently retry on network error
          pollTimer = setTimeout(pollProgress, POLL_INTERVAL);
        });
      }

      pollTimer = setTimeout(pollProgress, POLL_INTERVAL);
    })();
  </script>
  {% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/watched_list.html
git commit -m "feat: add silent JS polling for enrichment progress"
```

---

### Task 7: Manual Integration Test

- [ ] **Step 1: Ensure the arq worker is running**

In a separate terminal:
```bash
arq worker.WorkerSettings
```

- [ ] **Step 2: Start the dev server**

```bash
python3 app.py
```

- [ ] **Step 3: Clear previous import data**

If you previously imported, clear the watched list or use a fresh user account.

- [ ] **Step 4: Upload the Letterboxd CSV**

Navigate to `/watched`, upload `~/Downloads/letterboxd-billbadminton-2026-04-12-21-16-utc/watched.csv`.

Verify:
- Flash message shows matched/unmatched counts
- Grid initially shows only already-enriched movies (likely few or none)
- Over the next few minutes, movie poster cards silently appear in the grid as enrichment completes
- No page reload occurs — cards append via JS

- [ ] **Step 5: Verify enrichment completes**

Wait 5-10 minutes. Check the grid — enriched movies should continue appearing.

Open browser DevTools Network tab, filter by `enrichment-progress`. Verify:
- Requests fire every ~10 seconds
- Responses include `html` with card markup when new movies are ready
- Eventually a response has `"done": true` and polling stops

- [ ] **Step 6: Run full test suite**

```bash
python3 -m pytest tests/ -v --timeout=30
```

Expected: all tests pass

- [ ] **Step 7: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for enrichment progress"
```
