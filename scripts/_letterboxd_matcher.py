"""
Shared matching logic for Letterboxd integration prototypes.

Connects to the project's MySQL database and matches (title, year) pairs
against the movie_candidates table to resolve tconsts.
"""
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiomysql


@dataclass
class MatchResult:
    total: int = 0
    matched: list = field(default_factory=list)  # [{name, year, tconst}, ...]
    unmatched: list = field(default_factory=list)  # [{name, year}, ...]
    elapsed: float = 0.0


def load_env():
    """Load .env file into os.environ."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _db_config() -> dict:
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "db": os.getenv("DB_NAME", "imdb"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "charset": "utf8mb4",
    }


async def match_films(films: list[dict], batch_size: int = 100) -> MatchResult:
    """
    Match a list of {name, year} dicts against movie_candidates.

    Uses batched queries: for each batch, builds a single query with
    OR-joined (primaryTitle = %s AND startYear = %s) clauses.
    """
    result = MatchResult(total=len(films))
    start = time.monotonic()

    conn = await aiomysql.connect(**_db_config())
    try:
        # Process in batches to avoid overly large queries
        remaining = {(f["name"], f["year"]): f for f in films}

        for i in range(0, len(films), batch_size):
            batch = films[i : i + batch_size]
            if not batch:
                break

            # Build OR conditions
            conditions = []
            params = []
            for f in batch:
                conditions.append("(primaryTitle = %s AND startYear = %s)")
                params.extend([f["name"], f["year"]])

            query = (
                "SELECT tconst, primaryTitle, startYear "
                "FROM movie_candidates "
                "WHERE " + " OR ".join(conditions)
            )

            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()

            for row in rows:
                key = (row["primaryTitle"], row["startYear"])
                if key in remaining:
                    result.matched.append(
                        {
                            "name": row["primaryTitle"],
                            "year": row["startYear"],
                            "tconst": row["tconst"],
                        }
                    )
                    del remaining[key]

        # Whatever is left is unmatched
        result.unmatched = [{"name": k[0], "year": k[1]} for k in remaining]
    finally:
        conn.close()

    result.elapsed = time.monotonic() - start
    return result


def print_results(result: MatchResult):
    """Print a summary of the match results."""
    matched_count = len(result.matched)
    pct = (matched_count / result.total * 100) if result.total else 0

    print(f"{'='*70}")
    print(f"MATCH RESULTS")
    print(f"{'='*70}")
    print(f"Total films:   {result.total}")
    print(f"Matched:       {matched_count} ({pct:.1f}%)")
    print(f"Unmatched:     {len(result.unmatched)}")
    print(f"Time:          {result.elapsed:.2f}s")
    print(f"{'='*70}\n")

    if result.matched:
        print(f"--- First 20 matched ---")
        print(f"{'Title':<50} {'Year':<6} {'tconst'}")
        print("-" * 75)
        for m in result.matched[:20]:
            print(f"{m['name']:<50} {m['year']:<6} {m['tconst']}")

    if result.unmatched:
        print(f"\n--- First 30 unmatched ---")
        print(f"{'Title':<50} {'Year'}")
        print("-" * 56)
        for u in result.unmatched[:30]:
            print(f"{u['name']:<50} {u['year']}")

        if len(result.unmatched) > 30:
            print(f"  ... and {len(result.unmatched) - 30} more")
