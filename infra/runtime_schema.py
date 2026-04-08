"""Runtime-owned MySQL tables used by the hardened request path."""

from __future__ import annotations

from logging_config import get_logger

logger = get_logger(__name__)
_CANDIDATE_GENRE_FULLTEXT_INDEX = "ftx_movie_candidates_genres"


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
        current_ref_json JSON NULL,
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
        shuffle_key INT NOT NULL,
        refreshed_at DATETIME(6) NOT NULL,
        KEY idx_movie_candidates_filter (titleType, startYear, averageRating, numVotes, sample_bucket),
        KEY idx_movie_candidates_language (language),
        KEY idx_movie_candidates_slug (slug(191)),
        KEY idx_movie_candidates_refreshed_at (refreshed_at),
        FULLTEXT KEY ftx_movie_candidates_genres (genres)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id       CHAR(32) PRIMARY KEY,
        email         VARCHAR(255) NOT NULL,
        password_hash VARCHAR(255) NULL,
        display_name  VARCHAR(100) NULL,
        auth_provider VARCHAR(20) NOT NULL DEFAULT 'email',
        oauth_sub     VARCHAR(255) NULL,
        created_at    DATETIME(6) NOT NULL,
        updated_at    DATETIME(6) NOT NULL,
        UNIQUE KEY idx_users_email (email),
        UNIQUE KEY idx_users_oauth (auth_provider, oauth_sub)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS user_watched_movies (
        user_id    CHAR(32) NOT NULL,
        tconst     VARCHAR(16) NOT NULL,
        watched_at DATETIME(6) NOT NULL,
        PRIMARY KEY (user_id, tconst),
        KEY idx_watched_user_date (user_id, watched_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


async def ensure_runtime_schema(db_pool) -> None:
    """Create runtime-owned tables if they do not already exist."""
    for statement in _RUNTIME_SCHEMA_STATEMENTS:
        await db_pool.execute(statement, fetch="none")
    await ensure_user_navigation_current_ref_column(db_pool)
    await ensure_movie_candidates_shuffle_key(db_pool)
    await ensure_movie_candidates_refreshed_at_index(db_pool)
    await ensure_movie_candidates_shuffle_key_index(db_pool)
    await ensure_popular_movies_cache_composite_index(db_pool)
    await ensure_user_navigation_user_id_column(db_pool)
    logger.info("Runtime schema ensured")


async def ensure_user_navigation_current_ref_column(db_pool) -> None:
    """Add the additive current_ref_json column for navigation state."""
    present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'user_navigation_state'
          AND column_name = 'current_ref_json'
        LIMIT 1
        """,
        fetch="one",
    )
    if present:
        return

    await db_pool.execute(
        """
        ALTER TABLE user_navigation_state
        ADD COLUMN current_ref_json JSON NULL AFTER current_tconst
        """,
        fetch="none",
    )
    logger.info("Added user_navigation_state.current_ref_json")


async def ensure_user_navigation_user_id_column(db_pool) -> None:
    """Add the additive user_id column to link sessions to user accounts."""
    present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'user_navigation_state'
          AND column_name = 'user_id'
        LIMIT 1
        """,
        fetch="one",
    )
    if present:
        return

    await db_pool.execute(
        """
        ALTER TABLE user_navigation_state
        ADD COLUMN user_id CHAR(32) NULL AFTER session_id,
        ADD KEY idx_nav_user_id (user_id)
        """,
        fetch="none",
    )
    logger.info("Added user_navigation_state.user_id")


async def ensure_movie_candidates_shuffle_key(db_pool) -> None:
    """Add and backfill the additive shuffle key column."""
    present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'movie_candidates'
          AND column_name = 'shuffle_key'
        LIMIT 1
        """,
        fetch="one",
    )
    if not present:
        await db_pool.execute(
            """
            ALTER TABLE movie_candidates
            ADD COLUMN shuffle_key INT NULL AFTER sample_bucket
            """,
            fetch="none",
        )
        logger.info("Added movie_candidates.shuffle_key")

    await db_pool.execute(
        """
        UPDATE movie_candidates
        SET shuffle_key = MOD(CAST(CRC32(tconst) AS UNSIGNED), 2147483647)
        WHERE shuffle_key IS NULL
        """,
        fetch="none",
    )
    await db_pool.execute(
        """
        ALTER TABLE movie_candidates
        MODIFY COLUMN shuffle_key INT NOT NULL
        """,
        fetch="none",
    )


async def ensure_movie_candidates_refreshed_at_index(db_pool) -> None:
    """Ensure movie_candidates has a cheap freshest-row lookup."""
    present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'movie_candidates'
          AND index_name = 'idx_movie_candidates_refreshed_at'
        LIMIT 1
        """,
        fetch="one",
    )
    if present:
        return

    await db_pool.execute(
        "CREATE INDEX idx_movie_candidates_refreshed_at ON movie_candidates (refreshed_at)",
        fetch="none",
    )
    logger.info("Added movie_candidates refreshed_at index")


async def ensure_movie_candidates_shuffle_key_index(db_pool) -> None:
    """Ensure shuffle_key has an index to support the hot candidate-fetch sort.

    movies/candidate_store.py orders candidate queries by
    (shuffle_key, numVotes DESC, averageRating DESC). Without this index
    MySQL filesorts on every fetch.
    """
    present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'movie_candidates'
          AND index_name = 'idx_movie_candidates_shuffle'
        LIMIT 1
        """,
        fetch="one",
    )
    if present:
        return

    await db_pool.execute(
        "CREATE INDEX idx_movie_candidates_shuffle "
        "ON movie_candidates (shuffle_key, numVotes, averageRating)",
        fetch="none",
    )
    logger.info("Added movie_candidates shuffle_key index")


async def ensure_popular_movies_cache_composite_index(db_pool) -> None:
    """Add a filter+rand composite index to popular_movies_cache if it exists.

    popular_movies_cache is defined in ops/production_db_optimization.sql
    and may not exist in dev environments. Check table presence first so
    dev bootstraps are cleanly no-op. Supports the filter+random queries
    in movies/query_builder.py:414-415 by covering both the WHERE predicate
    prefix and the ORDER BY suffix in a single index.
    """
    table_present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = 'popular_movies_cache'
        LIMIT 1
        """,
        fetch="one",
    )
    if not table_present:
        logger.debug("popular_movies_cache not present; skipping composite index")
        return

    index_present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'popular_movies_cache'
          AND index_name = 'idx_cache_filter_rand'
        LIMIT 1
        """,
        fetch="one",
    )
    if index_present:
        return

    await db_pool.execute(
        "CREATE INDEX idx_cache_filter_rand "
        "ON popular_movies_cache (startYear, averageRating, numVotes, rand_order)",
        fetch="none",
    )
    logger.info("Added popular_movies_cache filter+rand composite index")


async def ensure_movie_candidates_fulltext_index(db_pool) -> None:
    """Repair the active movie_candidates FULLTEXT index when it is missing."""
    row = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'movie_candidates'
          AND index_name = %s
          AND column_name = 'genres'
          AND index_type = 'FULLTEXT'
        LIMIT 1
        """,
        [_CANDIDATE_GENRE_FULLTEXT_INDEX],
        fetch="one",
    )
    if row:
        return

    logger.warning(
        "movie_candidates FULLTEXT index %s missing; attempting repair",
        _CANDIDATE_GENRE_FULLTEXT_INDEX,
    )
    try:
        await db_pool.execute(
            f"ALTER TABLE movie_candidates ADD FULLTEXT KEY {_CANDIDATE_GENRE_FULLTEXT_INDEX} (genres)",
            fetch="none",
        )
    except Exception as exc:  # pragma: no cover - depends on DB privileges/runtime engine
        logger.warning(
            "Unable to repair movie_candidates FULLTEXT index %s: %s",
            _CANDIDATE_GENRE_FULLTEXT_INDEX,
            exc,
        )
    else:
        logger.info(
            "Repaired movie_candidates FULLTEXT index %s",
            _CANDIDATE_GENRE_FULLTEXT_INDEX,
        )
