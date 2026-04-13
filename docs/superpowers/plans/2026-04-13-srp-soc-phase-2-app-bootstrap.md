# SRP/SoC Phase 2 App Bootstrap Implementation Plan

**Goal:** Split `nextreel/web/app.py` so `create_app()` composes focused collaborators instead of owning Redis setup, job queue setup, request context, lifecycle, and MovieManager dependency construction.

**Architecture:** Keep root `app.py` and `nextreel/web/app.py:create_app()` as compatibility entry points. Move implementation into modules with one reason to change.

## File Structure

- Create `nextreel/bootstrap/movie_manager_factory.py` for MovieManager dependency construction.
- Create `nextreel/infra/redis_runtime.py` for Redis URL resolution, session setup, and cache setup.
- Create `nextreel/infra/job_queue.py` for lazy ARQ pool creation and enqueue metrics.
- Create `nextreel/web/request_context.py` for navigation-state request hooks, cookie persistence, security headers, and slow-request sampling.
- Create `nextreel/web/lifecycle.py` for startup warm-up and shutdown.
- Modify `nextreel/web/app.py` to call these modules.

## Tasks

- [x] Add failing unit tests for the extracted factory, Redis URL resolution, job queue enqueue behavior, request-context test state, and lifecycle startup.
- [x] Implement the extracted modules.
- [x] Replace inline app bootstrap logic with module calls.
- [x] Update app tests to patch the new owner modules instead of patching implementation details on `app.py`.
- [x] Run focused app/bootstrap tests.
- [x] Run full `pytest`.
