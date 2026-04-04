"""Shared referential-integrity checks for runtime and CLI use."""

from __future__ import annotations

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
