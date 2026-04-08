---
name: nav-state-migration-check
description: Check the status of the Redis→MySQL navigation state dual-write migration and flag when it is safe to flip NAV_STATE_DUAL_WRITE_ENABLED off
user-invocable: false
---

# Navigation State Migration Check

Claude-invoked background skill. Use whenever the conversation touches `infra/navigation_state.py`, `movie_navigator.py`, `NAV_STATE_DUAL_WRITE_ENABLED`, session cookies, or any navigation route (`/next_movie`, `/previous_movie`). The goal is to prevent the dual-write from living past its intended 7-day window and causing write-amplification on every navigation mutation.

## Context (load this into working memory)

From CLAUDE.md:
- Navigation state is **MySQL-backed** in `user_navigation_state` via `NavigationStateStore` with optimistic locking (version column, 5 retries, exponential backoff + jitter).
- Dual-write from Redis session → MySQL is **on by default** for a 7-day migration period.
- Flag: `NAV_STATE_DUAL_WRITE_ENABLED` (default `true`).
- Ops instruction: flip to `false` once `NAV_STATE_MIGRATION_MIN_DAYS` has elapsed.

## What to check

### 1. Has the migration window elapsed?

Find when dual-write was introduced:

```bash
git log --format="%aI %H %s" -- infra/navigation_state.py | head -20
git log -S "NAV_STATE_DUAL_WRITE_ENABLED" --format="%aI %H %s"
```

Compare the earliest commit date to today. If > `NAV_STATE_MIGRATION_MIN_DAYS` (default 7), the window has passed.

### 2. Is the flag still true?

```bash
grep -rn "NAV_STATE_DUAL_WRITE_ENABLED" --include="*.py" --exclude-dir=venv
```

Check the default and any deployment config. If the runtime default is still `true` *and* the window has elapsed, surface this to the user.

### 3. Is there any remaining reader code that depends on the Redis copy?

```bash
grep -rn "session\[.*nav" --include="*.py" --exclude-dir=venv
grep -rn "CURRENT_MOVIE_KEY\|PREV_MOVIES_KEY\|NEXT_MOVIES_KEY" --include="*.py" --exclude-dir=venv
```

Dual-write is only safe to disable if all readers have been cut over to `NavigationStateStore`. Any lingering `session[...]` read of a nav key is a blocker.

### 4. Are there recent optimistic-locking retries in logs?

A high retry rate on `user_navigation_state` version conflicts is an early warning that the MySQL path is under contention and you do NOT want to flip the flag mid-incident. Mention this in the report if the user has logs available.

## Output

Report a structured verdict:

- **Window status**: elapsed / not elapsed (with commit date)
- **Flag default**: true / false
- **Readers migrated**: yes / no (with grep evidence if 'no')
- **Recommendation**: one of
  - "Safe to flip — window elapsed, no lingering readers, no observed contention"
  - "Keep dual-write on — [specific reason]"
  - "Insufficient signal — ask ops to check [specific thing]"

Never *actually* flip the flag. This skill is read-only advisory. Changing deployment config is an ops action and belongs to the user.

## Why this skill is Claude-only (`user-invocable: false`)

This is background knowledge Claude should automatically consult when navigation code is touched, not a task the user manually triggers. There is no user-facing command — the skill exists to keep Claude from silently helping extend a migration window that was meant to be short.
