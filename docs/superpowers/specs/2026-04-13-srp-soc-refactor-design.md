# SRP and SoC Refactor Design

## Goal

Reduce the largest Single Responsibility Principle and Separation of Concerns violations without changing user-facing behavior. The work is intentionally phased so each phase can be tested and reviewed independently.

## Scope

This design covers the ten audit findings from the SRP/SoC review:

- `nextreel/web/app.py:create_app`
- `infra/navigation_state.py`
- `nextreel/web/routes/watched.py:import_letterboxd`
- `nextreel/web/routes/watched.py:enrichment_progress`
- `movies/projection_enrichment.py:ProjectionEnrichmentCoordinator.enrich_projection`
- `movies/query_builder.py:ImdbRandomMovieFetcher`
- `movies/candidate_store.py:CandidateStore`
- `movies/tmdb_client.py:TMDbHelper`
- `nextreel/application/movie_service.py:MovieManager`
- inline scripts in `templates/watched_list.html` and `templates/movie_card.html`

The refactor must keep root-level compatibility modules such as `app.py`, `routes.py`, and `movie_service.py` intact unless a targeted test proves they can be changed safely. These files are intentional import shims.

## Non-Goals

- No visual redesign.
- No database schema change unless an existing behavior already requires it.
- No route URL change.
- No replacement of Quart, Redis, ARQ, aiomysql, or TMDb.
- No broad formatting churn.

## Architecture

The refactor uses incremental extraction. Existing public entry points remain stable while implementation moves behind focused classes and functions.

Each extracted module has one primary reason to change:

- Web route modules translate HTTP inputs and service outcomes.
- Application services own workflows and domain decisions.
- Repository modules own SQL and row retrieval.
- Presenters own view-model formatting.
- Infrastructure adapters own Redis, ARQ, lifecycle, metrics, and framework glue.
- Provider clients own provider transport, while provider parsers and formatters stay separate.

## Phase 1: Watched and Letterboxd Boundary

Phase 1 extracts the route-heavy watched import flow first.

Create `nextreel/application/letterboxd_import_service.py` to own:

- CSV size and presence validation outcome mapping.
- Parsing via `movies.letterboxd_import.parse_watched_csv`.
- Film matching via an injected matcher.
- Bulk watched insertion via `WatchedStore`.
- Fire-and-forget enrichment scheduling intent.
- Import summary and unmatched-film data.

Create or extend a progress service that owns:

- Reading and updating import progress state from the Quart session mapping.
- Querying newly ready imported movies through a repository method instead of direct route SQL.
- Producing a public watched-card view model without calling a presenter's private method.

Route handlers in `nextreel/web/routes/watched.py` should only:

- Enforce login and CSRF/rate decorators.
- Read request files.
- Call the application service.
- Flash messages or return JSON.
- Render templates or partials.

Expected tests:

- Service tests for empty upload, oversized upload, invalid CSV, no films, partial match, full match, and enrichment scheduling.
- Route tests showing `import_letterboxd` delegates workflow behavior instead of doing matching/persistence inline.
- Progress tests showing newly ready cards are returned and session state is cleared when complete.

## Phase 2: App Bootstrap Boundary

Split `nextreel/web/app.py` into focused infrastructure modules:

- `nextreel/web/app.py`: thin `create_app()` and development server entry point.
- `nextreel/bootstrap/movie_manager_factory.py`: dependency composition for `MovieManager`.
- `nextreel/infra/redis_runtime.py`: Redis URL resolution, session install, cache install.
- `nextreel/infra/job_queue.py`: lazy ARQ pool and `enqueue_runtime_job`.
- `nextreel/web/request_context.py`: before/after request navigation state and cookie handling.
- `nextreel/web/lifecycle.py`: startup warm-up and shutdown resource draining.

The current behavior of degraded Redis mode, lazy ARQ, metrics counters, security headers, and startup candidate refresh must remain.

Expected tests:

- App construction smoke tests still pass.
- Unit tests for degraded Redis runtime.
- Unit tests for no-pool enqueue fallback.
- Request-context tests for test navigation state and cookie setting.

## Phase 3: Navigation State Boundary

Split `infra/navigation_state.py` into model, repository, migration, and service modules while keeping import compatibility.

Target files:

- `nextreel/domain/navigation_state.py`: `NavigationState`, `MutationResult`, constants, ref normalization.
- `infra/navigation_state_repository.py`: MySQL CRUD, JSON row mapping, Redis read-through cache.
- `nextreel/application/navigation_state_service.py`: fresh state, expiry, load-for-request, optimistic mutation, user binding.
- `infra/navigation_state.py`: compatibility exports only after the split.

Legacy migration remains in `infra/legacy_migration.py`, but `NavigationStateService` should depend on a migration object rather than importing migration details.

Expected tests:

- Existing `tests/infra/test_navigation_state.py`.
- Import compatibility tests.
- Focused tests for repository row mapping, service mutation conflict retry, legacy import, and user binding.

## Phase 4: Movie, Projection, Query, and Provider Boundaries

Split large domain/provider classes without changing route behavior.

Projection enrichment:

- Keep `ProjectionEnrichmentCoordinator` focused on dedupe, queueing, local task lifecycle, and shutdown.
- Move enrichment execution into `ProjectionEnrichmentService`.
- Move payload equality into `ProjectionPayloadDiffer`.
- Move persistence-mode decision into `ProjectionStatePolicy` or a small result factory.

Random movie query:

- Keep `MovieQueryBuilder` for SQL string construction only.
- Move count cache and single-flight locking to `MovieCountCache`.
- Move SQL execution to `RandomMovieRepository`.
- Keep `ImdbRandomMovieFetcher` as a thin orchestrator or compatibility facade.

Candidate selection:

- Split `CandidateRepository`, `CandidateSelector`, `CandidateQueryBuilder`, and `CandidateFilterPoolCache`.
- Keep `CandidateTableMaintainer` separate from read-path selection.

TMDb:

- Split transport/resilience from endpoint methods and metrics.
- Introduce `TMDbTransport`, `TMDbMetricsRecorder`, and `TMDbMovieApi`.
- Keep `TMDbHelper` as a compatibility facade until callers migrate.

Expected tests:

- Existing projection, query builder, candidate store, and TMDb tests.
- New unit tests for payload diffing, count-cache lock behavior, candidate query planning, and TMDb metric recorder label behavior.

## Phase 5: Template Script Boundary

Move inline scripts into focused static JavaScript modules.

Target files:

- `static/js/movie-card.js`: genre formatting, vote formatting, trailer opener, watched toggle, navigation submit state.
- `static/js/watched-list.js`: search, sort, remove mutation, toast.
- `static/js/watched-enrichment-progress.js`: polling and card insertion.

Templates should render data and include scripts, not own client-side controllers.

Expected tests:

- Existing web route/view tests still pass.
- Add lightweight static asset presence tests.
- Browser verification can be added later if visual behavior changes, but this phase should avoid visual changes.

## Compatibility Rules

- Public route names remain unchanged.
- Root import shims remain valid.
- Existing tests under `tests/` are the verification baseline.
- Private methods can be preserved temporarily only when existing tests patch them. New code must prefer public collaborators.
- Any behavior-preserving facade must be marked as compatibility in docstrings so it does not become the new home for more logic.

## Error Handling

Application services return structured outcomes. Routes decide how to map outcomes to flashes, redirects, HTTP statuses, or JSON.

Infrastructure failures keep current degraded-mode behavior:

- Redis unavailable means Redis-dependent cache/session/job features degrade.
- ARQ unavailable means enqueue returns `None` and metrics record fallback.
- TMDb failures continue to produce core or failed projection payload behavior.

## Testing Strategy

Use TDD per phase:

1. Add failing tests for the target boundary.
2. Move logic behind a focused collaborator.
3. Keep public behavior stable.
4. Run the targeted tests.
5. Run `pytest` after each phase.

For template script extraction, verify that generated pages include the static script and no route contract changed.

## Rollout Order

1. Phase 1: watched/Letterboxd routes and progress service.
2. Phase 2: app bootstrap split.
3. Phase 3: navigation state split.
4. Phase 4: movie/projection/query/provider split.
5. Phase 5: static JavaScript extraction.

This order reduces risk by extracting high-churn route logic before central infrastructure and persistence state.
