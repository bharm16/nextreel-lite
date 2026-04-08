"""Database tech debt cleanup — drop dead tables, redundant indexes, shrink varchars.

Every operation is idempotent: checks preconditions before acting, skips
if already applied, and logs what it does.  Safe to re-run.

Usage:
    python -m scripts.db_cleanup                         # dry-run (default)
    python -m scripts.db_cleanup --execute               # apply changes
    python -m scripts.db_cleanup --execute --skip-alter   # skip slow ALTER TABLE ops
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import aiomysql

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from logging_config import get_logger, setup_logging  # noqa: E402

setup_logging()
logger = get_logger(__name__)


async def _get_connection() -> aiomysql.Connection:
    return await aiomysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        db=os.getenv("DB_NAME", "imdb"),
        port=int(os.getenv("DB_PORT", "3306")),
        autocommit=True,
    )


async def _table_exists(cur: aiomysql.Cursor, table: str) -> bool:
    await cur.execute(
        "SELECT 1 FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return await cur.fetchone() is not None


async def _index_exists(cur: aiomysql.Cursor, table: str, index: str) -> bool:
    await cur.execute(
        "SELECT 1 FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
        (table, index),
    )
    return await cur.fetchone() is not None


async def _column_type(cur: aiomysql.Cursor, table: str, column: str) -> str | None:
    await cur.execute(
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    row = await cur.fetchone()
    return row[0] if row else None


# ── Phase 1: Drop dead tables ───────────────────────────────────────

DEAD_TABLES = ["title.akastest", "title.akas"]


async def drop_dead_tables(cur: aiomysql.Cursor, *, dry_run: bool) -> None:
    for table in DEAD_TABLES:
        if not await _table_exists(cur, table):
            print(f"  SKIP  {table!r} — already gone")
            continue
        sql = f"DROP TABLE `{table}`"
        if dry_run:
            print(f"  DRY   {sql}")
        else:
            start = time.time()
            await cur.execute(sql)
            print(f"  DROP  {table!r} ({time.time() - start:.1f}s)")


# ── Phase 2–6: Drop redundant indexes ───────────────────────────────

REDUNDANT_INDEXES: list[tuple[str, str, str]] = [
    # (table, index_name, reason)
    ("title.ratings", "idx_tconst", "duplicates PRIMARY(tconst)"),
    ("title.ratings", "idx_tconst_ratings", "duplicates PRIMARY(tconst)"),
    ("title.ratings", "idx_title_ratings_tconst", "duplicates PRIMARY(tconst)"),
    ("title.ratings", "idx_numVotes", "prefix of idx_ratings_compound(numVotes, averageRating)"),
    ("title.basics", "idx_tconst", "duplicates PRIMARY(tconst)"),
    ("title.basics", "idx_tconst_basics", "duplicates PRIMARY(tconst)"),
    ("title.crew", "idx_title_crew_tconst", "duplicates PRIMARY(tconst)"),
    ("title.principals", "idx_tconst_nconst", "duplicates PRIMARY(tconst, nconst)"),
    ("recent_movies_cache", "idx_recent_year", "prefix of idx_recent_filter(startYear, ...)"),
]


async def drop_redundant_indexes(cur: aiomysql.Cursor, *, dry_run: bool) -> None:
    for table, index, reason in REDUNDANT_INDEXES:
        if not await _index_exists(cur, table, index):
            print(f"  SKIP  `{table}`.{index} — already gone")
            continue
        sql = f"DROP INDEX `{index}` ON `{table}`"
        if dry_run:
            print(f"  DRY   {sql}  -- {reason}")
        else:
            start = time.time()
            await cur.execute(sql)
            print(f"  DROP  `{table}`.{index} ({time.time() - start:.1f}s) -- {reason}")


# ── Phase 7: Shrink varchar(255) → varchar(16) ──────────────────────

VARCHAR_SHRINKS: list[tuple[str, str]] = [
    ("title.basics", "tconst"),
    ("title.ratings", "tconst"),
    ("title.crew", "tconst"),
    ("title.episode", "tconst"),
    ("title.principals", "tconst"),
    ("title.principals", "nconst"),
    ("name.basics", "nconst"),
]

TARGET_TYPE = "varchar(16)"


async def shrink_varchars(cur: aiomysql.Cursor, *, dry_run: bool) -> None:
    for table, column in VARCHAR_SHRINKS:
        current = await _column_type(cur, table, column)
        if current is None:
            print(f"  SKIP  `{table}`.{column} — column not found")
            continue
        if current == TARGET_TYPE:
            print(f"  SKIP  `{table}`.{column} — already {TARGET_TYPE}")
            continue
        sql = f"ALTER TABLE `{table}` MODIFY `{column}` VARCHAR(16) NOT NULL"
        if dry_run:
            print(f"  DRY   {sql}  -- currently {current}")
        else:
            print(f"  ALTER `{table}`.{column} {current} -> {TARGET_TYPE} ... ", end="", flush=True)
            start = time.time()
            await cur.execute(sql)
            print(f"done ({time.time() - start:.1f}s)")


# ── Phase 8: Purge expired navigation sessions ──────────────────────

async def purge_expired_sessions(cur: aiomysql.Cursor, *, dry_run: bool) -> None:
    await cur.execute(
        "SELECT COUNT(*) FROM user_navigation_state WHERE expires_at < UTC_TIMESTAMP(6)"
    )
    (count,) = await cur.fetchone()
    if count == 0:
        print("  SKIP  no expired navigation sessions")
        return
    if dry_run:
        print(f"  DRY   DELETE {count} expired navigation sessions")
    else:
        await cur.execute(
            "DELETE FROM user_navigation_state WHERE expires_at < UTC_TIMESTAMP(6)"
        )
        print(f"  PURGE {count} expired navigation sessions")


# ── Main ─────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Database tech debt cleanup")
    parser.add_argument(
        "--execute", action="store_true",
        help="Apply changes (default is dry-run)",
    )
    parser.add_argument(
        "--skip-alter", action="store_true",
        help="Skip ALTER TABLE operations (varchar shrink) — useful for quick runs",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    if dry_run:
        print("=== DRY RUN (pass --execute to apply) ===\n")
    else:
        print("=== EXECUTING CHANGES ===\n")

    conn = await _get_connection()
    try:
        async with conn.cursor() as cur:
            print("[Phase 1] Drop dead tables")
            await drop_dead_tables(cur, dry_run=dry_run)

            print("\n[Phase 2-6] Drop redundant indexes")
            await drop_redundant_indexes(cur, dry_run=dry_run)

            if args.skip_alter:
                print("\n[Phase 7] Shrink varchar(255) -> varchar(16) — SKIPPED (--skip-alter)")
            else:
                print("\n[Phase 7] Shrink varchar(255) -> varchar(16)")
                if not dry_run:
                    print("  (This may take several minutes on large tables)")
                await shrink_varchars(cur, dry_run=dry_run)

            print("\n[Phase 8] Purge expired navigation sessions")
            await purge_expired_sessions(cur, dry_run=dry_run)
    finally:
        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
