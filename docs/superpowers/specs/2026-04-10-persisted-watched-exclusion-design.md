# Persisted Watched Exclusion Preference

## Summary

When a logged-in user marks a movie as watched from the movie page, that movie should stop appearing in future random discovery results as long as the `Exclude watched` filter is enabled. That filter should be persisted to the user's account and default to `on` for every newly created account. The current movie page should remain visible after the watched toggle; marking a movie as watched is not a navigation action.

This design keeps three concerns separate:

1. `user_watched_movies` remains the durable source of truth for which movies a user has seen.
2. A new account-level preference on `users` becomes the durable source of truth for whether watched movies should be excluded by default.
3. `user_navigation_state.filters_json` remains session-scoped discovery state and is seeded from the account preference when a user attaches to a session.

## Goals

- Exclude watched movies from future random discovery when the filter is on.
- Persist the `Exclude watched` choice across visits and link it to the user account.
- Default the preference to `on` for all new accounts.
- Keep the current movie page visible after `Mark as Watched`.
- Preserve the existing filter drawer interaction model: preference changes save only on `Apply filters`.

## Non-Goals

- No auto-navigation after watched toggles.
- No watched exclusion for anonymous users.
- No generic preferences framework for unrelated settings.
- No change to explicit history navigation semantics (`Previous` and `future` stacks stay user-directed).

## Current State

- Watched movies are already stored durably in `user_watched_movies`.
- The filter form and filter drawer already expose `exclude_watched`.
- `MovieNavigator._refill_queue()` already merges watched tconsts into the excluded set when `state.filters["exclude_watched"]` is true.
- Filter state currently lives in `user_navigation_state.filters_json`, which is session state, not durable account preference.
- The watched toggle on the movie page updates watched state asynchronously but does not affect navigation.

The gap is that the filter preference is not account-scoped, and stale queue entries can still leak watched titles back into discovery if they were queued before the watched mutation happened.

## Recommended Approach

Add a narrow, explicit boolean account preference on `users` named `exclude_watched_default` and default it to `true`.

This is preferred over a generic `user_preferences` table because the repo does not yet have multiple durable preferences that justify the extra abstraction. Reusing `user_navigation_state.filters_json` as the durable source of truth is incorrect because that table represents expiring session state and navigation context, not account preferences.

## Data Model

### `users`

Add:

- `exclude_watched_default BOOLEAN NOT NULL DEFAULT TRUE`

Behavior:

- Newly created email/password users get `TRUE`.
- Newly created OAuth users get `TRUE`.
- Existing rows are backfilled to `TRUE` by the additive schema migration.

### `user_watched_movies`

No schema change.

This table remains the durable watched-list store keyed by `(user_id, tconst)`.

### `user_navigation_state`

No schema change.

`filters_json` remains a session copy of the active filter state. It is seeded from the account preference when a user becomes associated with the session.

## State Ownership

### Durable account state

- `users.exclude_watched_default`
- `user_watched_movies`

### Session-scoped discovery state

- `user_navigation_state.filters_json`
- queue / prev / future / seen navigation structures

Rule:

- The account preference seeds the session filter.
- The session filter governs current discovery behavior.
- A successful `Apply filters` request updates both the session filter and the account preference for logged-in users.

## Behavior

### Account creation

On both registration paths:

- Email/password registration inserts `exclude_watched_default = TRUE`.
- OAuth account creation inserts `exclude_watched_default = TRUE`.

### Login / register / OAuth attach

When a user is attached to the current session:

1. Persist `user_id` on the navigation state as today.
2. Load the account preference from `users.exclude_watched_default`.
3. Overwrite `state.filters["exclude_watched"]` and the persisted `filters_json` copy with that value.

This prevents stale anonymous or prior-session filter state from bleeding into the logged-in user's discovery session.

### Filter drawer submit

When the logged-in user submits the drawer or full filter form to `/filtered_movie`:

1. Normalize and validate the submitted filters.
2. If validation fails, render errors and persist nothing.
3. If validation succeeds:
   - persist `filters["exclude_watched"]` to the user account preference
   - apply the full filter set to the current session
   - reset queue/history as current `apply_filters()` already does
   - redirect or return JSON as current flow already does

Preference persistence happens only on `Apply filters`, never on checkbox change.

### Movie page watched toggle

When the user clicks `Mark as Watched`:

- Add the movie to `user_watched_movies`.
- Keep the current movie page visible.
- Do not navigate to the next movie.
- Do not persist any change to `exclude_watched_default`.

Effect:

- If the session filter is on, the current movie becomes ineligible for future random discovery.
- If the session filter is off, watched movies may still appear in future random discovery until the user turns the filter back on and applies.

### Next / random discovery behavior

The navigator must not rely only on queue refill exclusion.

Required behavior:

- Before serving a movie from the queued refs, if `exclude_watched` is on for a logged-in user, discard queued entries whose `tconst` is now in the watched set.
- Continue popping/skipping until an unwatched candidate is found or the queue is exhausted.
- If the queue exhausts after skipping watched entries, refill using the watched exclusion set and continue as normal.

This closes the stale-queue bug where a movie can be queued before it is marked watched and still be shown later.

### Previous / future history

No watched-based pruning of `prev` or `future`.

Reason:

- History stacks represent explicit navigation choices.
- Discovery exclusion should affect future random picks, not the user's ability to revisit a previously viewed page through history navigation.

## Architecture Changes

### New narrow account-preference access layer

Introduce a focused helper module at `session/user_preferences.py` rather than embedding SQL directly in routes.

Responsibilities:

- read `exclude_watched_default` for a user id
- update `exclude_watched_default` for a user id

This module stays narrowly scoped to this single preference.

### Auth flow integration

Add a shared auth-session attach helper used by login, register, and OAuth callback flows. That helper must:

1. call `set_user_id()`
2. load `exclude_watched_default` from `session/user_preferences.py`
3. write that value into the in-memory state and persisted `filters_json`

This avoids duplicating synchronization logic across auth routes.

### Filter apply integration

Extend the successful `/filtered_movie` path to persist the logged-in user's `exclude_watched` choice before applying filters to navigation state.

### Navigator hardening

Update `MovieNavigator.next_movie()` or a private helper used by it to skip watched refs already sitting in `state.queue` when watched exclusion is enabled.

This is the minimum reliable fix. Solving it only at queue refill time is incomplete.

## Failure Handling

### Invalid filter submission

- Return validation errors as today.
- Do not persist the account preference.
- Do not update session filter state.

### Preference persistence failure on valid apply

- Treat the request as failed.
- Do not proceed with partially successful behavior where session filters change but the account preference does not.

Reason:

- The UI promise is that `Apply` saves the current choice for future visits. Silent partial success would violate that contract.

### Watched mutation failure

- Keep current page in place.
- Preserve current async status/error messaging behavior.
- Do not attempt fallback navigation.

## Testing

### Unit tests

- Schema/runtime tests covering the new `users.exclude_watched_default` additive column.
- Preference store/helper tests for read and update behavior.
- Auth creation tests proving new email and OAuth users receive `exclude_watched_default = TRUE`.
- Route tests for `/filtered_movie`:
  - logged-in successful apply persists the preference
  - validation failure does not persist the preference
  - anonymous apply does not try to persist account preference
- Navigator tests proving watched movies already in queue are skipped when `exclude_watched` is on.

### Integration tests

- End-to-end logged-in flow:
  - start with `exclude_watched = on`
  - mark current movie watched
  - stay on the current page
  - request next/random movie
  - confirm the watched movie does not reappear through random discovery
- Persistence flow:
  - logged-in user turns `Exclude watched` off and hits `Apply`
  - logout / new session / login
  - confirm the drawer loads with `Exclude watched` off

## Affected Areas

- `infra/runtime_schema.py`
- `session/user_auth.py`
- new helper module `session/user_preferences.py`
- shared auth attach flow used by `nextreel/web/routes/auth.py`
- `nextreel/web/routes/navigation.py`
- `infra/navigation_state.py`
- `nextreel/application/movie_navigator.py`
- tests covering runtime schema, auth, navigation routes, and navigator behavior

## Decisions Locked

- `Mark as Watched` keeps the current movie page visible.
- The watched exclusion preference is account-linked and persists across future visits.
- The preference saves only when the user hits `Apply filters`.
- The preference defaults to `on` upon account creation.
- Discovery exclusion affects future random picks, not explicit history navigation.

## Out of Scope

- Building a generic cross-feature preferences system.
- Adding anonymous watched tracking.
- Reworking the overall filter UX beyond the existing checkbox and drawer behavior.
