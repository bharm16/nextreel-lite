"""Validate referential integrity across IMDb tables.

Since the IMDb dataset tables lack foreign key constraints, this script
checks for orphan records that would violate referential integrity.

Run periodically after data imports to catch inconsistencies early.

Usage:
    python -m scripts.validate_referential_integrity

See ADR-001-ARCHITECTURE-AUDIT.md, Finding 2.4.
"""

import asyncio
import sys

from logging_config import get_logger

logger = get_logger(__name__)

# Each check is (description, query that returns orphan count).
INTEGRITY_CHECKS = [
    (
        "title.ratings referencing non-existent title.basics",
        """
        SELECT COUNT(*) AS orphans
        FROM `title.ratings` tr
        LEFT JOIN `title.basics` tb ON tr.tconst = tb.tconst
        WHERE tb.tconst IS NULL
        """,
    ),
    (
        "title.crew referencing non-existent title.basics",
        """
        SELECT COUNT(*) AS orphans
        FROM `title.crew` tc
        LEFT JOIN `title.basics` tb ON tc.tconst = tb.tconst
        WHERE tb.tconst IS NULL
        """,
    ),
    (
        "title.principals referencing non-existent title.basics",
        """
        SELECT COUNT(*) AS orphans
        FROM `title.principals` tp
        LEFT JOIN `title.basics` tb ON tp.tconst = tb.tconst
        WHERE tb.tconst IS NULL
        """,
    ),
    (
        "title.principals referencing non-existent name.basics",
        """
        SELECT COUNT(*) AS orphans
        FROM `title.principals` tp
        LEFT JOIN `name.basics` nb ON tp.nconst = nb.nconst
        WHERE tp.nconst IS NOT NULL AND nb.nconst IS NULL
        """,
    ),
    (
        "popular_movies_cache referencing non-existent title.basics",
        """
        SELECT COUNT(*) AS orphans
        FROM popular_movies_cache pmc
        LEFT JOIN `title.basics` tb ON pmc.tconst = tb.tconst
        WHERE tb.tconst IS NULL
        """,
    ),
    (
        "recent_movies_cache referencing non-existent title.basics",
        """
        SELECT COUNT(*) AS orphans
        FROM recent_movies_cache rmc
        LEFT JOIN `title.basics` tb ON rmc.tconst = tb.tconst
        WHERE tb.tconst IS NULL
        """,
    ),
]


async def run_checks():
    from db_utils import init_pool
    from database.errors import DatabaseError

    db_pool = await init_pool()
    issues_found = 0

    for description, query in INTEGRITY_CHECKS:
        try:
            result = await db_pool.execute(query, fetch="one")
            orphan_count = result.get("orphans", 0) if result else 0
            if orphan_count > 0:
                logger.warning("INTEGRITY ISSUE: %s — %d orphan records", description, orphan_count)
                issues_found += 1
            else:
                logger.info("OK: %s", description)
        except DatabaseError as e:
            logger.error("CHECK FAILED: %s — %s", description, e)
            issues_found += 1

    await db_pool.close_pool()

    if issues_found:
        logger.warning("Referential integrity check completed with %d issue(s)", issues_found)
    else:
        logger.info("All referential integrity checks passed")

    return issues_found


if __name__ == "__main__":
    issues = asyncio.run(run_checks())
    sys.exit(1 if issues else 0)
