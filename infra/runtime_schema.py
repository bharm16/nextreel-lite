"""Runtime-owned MySQL tables used by the hardened request path."""

from __future__ import annotations

from logging_config import get_logger

logger = get_logger(__name__)


_RUNTIME_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runtime_metadata (
        meta_key VARCHAR(128) PRIMARY KEY,
        meta_value TEXT NOT NULL,
        updated_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS user_navigation_state (
        session_id VARCHAR(64) PRIMARY KEY,
        version INT NOT NULL DEFAULT 1,
        csrf_token VARCHAR(128) NOT NULL,
        filters_json JSON NOT NULL,
        current_tconst VARCHAR(16) NULL,
        queue_json JSON NOT NULL,
        prev_json JSON NOT NULL,
        future_json JSON NOT NULL,
        seen_json JSON NOT NULL,
        created_at DATETIME(6) NOT NULL,
        last_activity_at DATETIME(6) NOT NULL,
        expires_at DATETIME(6) NOT NULL,
        KEY idx_user_navigation_expires_at (expires_at),
        KEY idx_user_navigation_last_activity (last_activity_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS movie_projection (
        tconst VARCHAR(16) PRIMARY KEY,
        tmdb_id BIGINT NULL,
        payload_json JSON NOT NULL,
        projection_state VARCHAR(16) NOT NULL,
        enriched_at DATETIME(6) NULL,
        stale_after DATETIME(6) NULL,
        last_attempt_at DATETIME(6) NULL,
        attempt_count INT NOT NULL DEFAULT 0,
        last_error TEXT NULL,
        KEY idx_movie_projection_state_stale (projection_state, stale_after),
        KEY idx_movie_projection_last_attempt (last_attempt_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS movie_candidates (
        tconst VARCHAR(16) PRIMARY KEY,
        primaryTitle VARCHAR(512) NOT NULL,
        startYear INT NOT NULL,
        genres TEXT NULL,
        language VARCHAR(16) NULL,
        titleType VARCHAR(32) NOT NULL,
        slug VARCHAR(512) NULL,
        averageRating DECIMAL(4,2) NOT NULL DEFAULT 0,
        numVotes INT NOT NULL DEFAULT 0,
        sample_bucket INT NOT NULL,
        refreshed_at DATETIME(6) NOT NULL,
        KEY idx_movie_candidates_filter (titleType, startYear, averageRating, numVotes, sample_bucket),
        KEY idx_movie_candidates_language (language),
        KEY idx_movie_candidates_slug (slug(191)),
        FULLTEXT KEY ftx_movie_candidates_genres (genres)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


async def ensure_runtime_schema(db_pool) -> None:
    """Create runtime-owned tables if they do not already exist."""
    for statement in _RUNTIME_SCHEMA_STATEMENTS:
        await db_pool.execute(statement, fetch="none")
    logger.info("Runtime schema ensured")
