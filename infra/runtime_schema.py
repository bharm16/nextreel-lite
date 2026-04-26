"""Runtime-owned MySQL tables used by the hardened request path."""

from __future__ import annotations

import sys

import pymysql

from logging_config import get_logger

logger = get_logger(__name__)
_CANDIDATE_GENRE_FULLTEXT_INDEX = "ftx_movie_candidates_genres"

_ERR_TABLE_EXISTS = 1050
_ERR_DUP_KEYNAME = 1061
_ERR_DUP_FIELDNAME = 1060


async def _execute_ddl(db_pool, sql: str) -> None:
    """Execute a DDL statement using the raw aiomysql pool.

    Bypasses SecureConnectionPool.acquire() so that expected idempotent
    errors (duplicate column/index) don't trigger circuit-breaker failures
    or get logged at ERROR by the pool's query wrapper.
    """
    conn = await db_pool.pool.pool.acquire()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(sql)
    finally:
        db_pool.pool.pool.release(conn)


async def _ensure_table(db_pool, table: str, create_sql: str) -> None:
    """Create a runtime-owned table when it is missing.

    MySQL reports ``CREATE TABLE IF NOT EXISTS`` on an existing table as a
    warning, and aiomysql surfaces that through Python warnings. Probe first
    so normal restarts do not emit warning spam, then run plain DDL only for
    missing tables.
    """
    table_present = await db_pool.execute(
        """
        SELECT 1 AS present
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = %s
        LIMIT 1
        """,
        [table],
        fetch="one",
    )
    if table_present:
        logger.debug("table %s already exists", table)
        return

    try:
        await _execute_ddl(db_pool, create_sql)
        logger.info("created table %s", table)
    except pymysql.err.OperationalError as exc:
        if exc.args and exc.args[0] == _ERR_TABLE_EXISTS:
            logger.debug("table %s already exists", table)
            return
        raise


async def _ensure_index(db_pool, table: str, name: str, create_sql: str) -> None:
    """Create an index if it doesn't already exist.

    Uses MySQL's duplicate-key errno (1061) as the 'already exists' signal
    instead of a SELECT probe — removes a round-trip per startup and closes
    a TOCTOU window between the probe and the create.
    """
    try:
        await _execute_ddl(db_pool, create_sql)
        logger.info("created index %s.%s", table, name)
    except pymysql.err.OperationalError as exc:
        if exc.args and exc.args[0] == _ERR_DUP_KEYNAME:
            logger.debug("index %s.%s already exists", table, name)
            return
        raise


async def _get_runtime_flag(db_pool, key: str) -> str | None:
    """Read a runtime_metadata flag value, or None if unset."""
    row = await db_pool.execute(
        "SELECT meta_value FROM runtime_metadata WHERE meta_key = %s",
        [key],
        fetch="one",
    )
    if not row:
        return None
    return row["meta_value"] if isinstance(row, dict) else row[0]


async def _set_runtime_flag(db_pool, key: str, value: str) -> None:
    """Upsert a runtime_metadata flag value."""
    from infra.time_utils import utcnow

    await db_pool.execute(
        """
        INSERT INTO runtime_metadata (meta_key, meta_value, updated_at)
        VALUES (%s, %s, %s)
        AS new_row
        ON DUPLICATE KEY UPDATE
            meta_value = new_row.meta_value,
            updated_at = new_row.updated_at
        """,
        [key, value, utcnow()],
        fetch="none",
    )


async def _ensure_column(db_pool, table: str, name: str, create_sql: str) -> None:
    """Add a column if it doesn't already exist.

    Uses MySQL's duplicate-column errno (1060) as the 'already exists' signal.
    """
    try:
        await _execute_ddl(db_pool, create_sql)
        logger.info("added column %s.%s", table, name)
    except pymysql.err.OperationalError as exc:
        if exc.args and exc.args[0] == _ERR_DUP_FIELDNAME:
            logger.debug("column %s.%s already exists", table, name)
            return
        raise


_RUNTIME_SCHEMA_TABLE_DEFINITIONS = (
    (
        "runtime_metadata",
        """
    CREATE TABLE runtime_metadata (
        meta_key VARCHAR(128) PRIMARY KEY,
        meta_value TEXT NOT NULL,
        updated_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "user_navigation_state",
        """
    CREATE TABLE user_navigation_state (
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
    ),
    (
        "movie_projection",
        """
    CREATE TABLE movie_projection (
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
    ),
    (
        "movie_candidates",
        """
    CREATE TABLE movie_candidates (
        tconst VARCHAR(16) PRIMARY KEY,
        primaryTitle VARCHAR(512) NOT NULL,
        startYear INT NOT NULL,
        genres TEXT NULL,
        language VARCHAR(128) NULL,
        titleType VARCHAR(32) NOT NULL,
        slug VARCHAR(512) NULL,
        averageRating DECIMAL(4,2) NOT NULL DEFAULT 0,
        numVotes INT NOT NULL DEFAULT 0,
        sample_bucket INT NOT NULL,
        shuffle_key INT NOT NULL,
        refreshed_at DATETIME(6) NOT NULL,
        KEY idx_movie_candidates_filter (titleType, startYear, averageRating, numVotes, sample_bucket),
        KEY idx_movie_candidates_bucket_filter (titleType, sample_bucket, numVotes, averageRating, startYear),
        KEY idx_movie_candidates_language (language),
        KEY idx_movie_candidates_slug (slug(191)),
        KEY idx_movie_candidates_refreshed_at (refreshed_at),
        FULLTEXT KEY ftx_movie_candidates_genres (genres)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "users",
        """
    CREATE TABLE users (
        user_id       CHAR(32) PRIMARY KEY,
        email         VARCHAR(255) NOT NULL,
        password_hash VARCHAR(255) NULL,
        display_name  VARCHAR(100) NULL,
        auth_provider VARCHAR(20) NOT NULL DEFAULT 'email',
        oauth_sub     VARCHAR(255) NULL,
        exclude_watched_default BOOLEAN NOT NULL DEFAULT TRUE,
        created_at    DATETIME(6) NOT NULL,
        updated_at    DATETIME(6) NOT NULL,
        UNIQUE KEY idx_users_email (email),
        UNIQUE KEY idx_users_oauth (auth_provider, oauth_sub)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "user_watched_movies",
        """
    CREATE TABLE user_watched_movies (
        user_id    CHAR(32) NOT NULL,
        tconst     VARCHAR(16) NOT NULL,
        watched_at DATETIME(6) NOT NULL,
        PRIMARY KEY (user_id, tconst),
        KEY idx_watched_user_date (user_id, watched_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "letterboxd_imports",
        """
    CREATE TABLE letterboxd_imports (
        import_id     CHAR(32) PRIMARY KEY,
        user_id       CHAR(32) NOT NULL,
        status        VARCHAR(16) NOT NULL,
        total_rows    INT NULL,
        processed     INT NOT NULL DEFAULT 0,
        matched       INT NOT NULL DEFAULT 0,
        skipped       INT NOT NULL DEFAULT 0,
        failed        INT NOT NULL DEFAULT 0,
        error_message TEXT NULL,
        created_at    DATETIME(6) NOT NULL,
        updated_at    DATETIME(6) NOT NULL,
        completed_at  DATETIME(6) NULL,
        KEY idx_letterboxd_user_created (user_id, created_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "user_watchlist",
        """
    CREATE TABLE user_watchlist (
        user_id  CHAR(32) NOT NULL,
        tconst   VARCHAR(16) NOT NULL,
        added_at DATETIME(6) NOT NULL,
        PRIMARY KEY (user_id, tconst),
        KEY idx_watchlist_user_added (user_id, added_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
)

_RUNTIME_SCHEMA_STATEMENTS = tuple(
    create_sql for _, create_sql in _RUNTIME_SCHEMA_TABLE_DEFINITIONS
)


async def ensure_runtime_schema(db_pool) -> None:
    """Create runtime-owned tables if they do not already exist.

    Repair helpers are dispatched in declaration order. Add a new column or
    index by appending its name to ``_RUNTIME_REPAIR_HELPER_NAMES`` below —
    no need to edit this orchestrator. Names (not function refs) are resolved
    against the module at call time so test patches via
    ``patch("infra.runtime_schema.X", ...)`` take effect. Tradeoff: a typo in
    the tuple fails at startup with ``AttributeError`` rather than at import
    time — acceptable because this runs on every boot.
    """
    for table, statement in _RUNTIME_SCHEMA_TABLE_DEFINITIONS:
        await _ensure_table(db_pool, table, statement)
    module = sys.modules[__name__]
    for name in _RUNTIME_REPAIR_HELPER_NAMES:
        await getattr(module, name)(db_pool)
    logger.info("Runtime schema ensured")


async def ensure_user_navigation_current_ref_column(db_pool) -> None:
    """Add the additive current_ref_json column for navigation state."""
    await _ensure_column(
        db_pool,
        "user_navigation_state",
        "current_ref_json",
        """
        ALTER TABLE user_navigation_state
        ADD COLUMN current_ref_json JSON NULL AFTER current_tconst
        """,
    )


async def ensure_user_navigation_user_id_column(db_pool) -> None:
    """Add the additive user_id column to link sessions to user accounts."""
    await _ensure_column(
        db_pool,
        "user_navigation_state",
        "user_id",
        """
        ALTER TABLE user_navigation_state
        ADD COLUMN user_id CHAR(32) NULL AFTER session_id,
        ADD KEY idx_nav_user_id (user_id)
        """,
    )


async def ensure_users_exclude_watched_default_column(db_pool) -> None:
    """Add the default watched-exclusion preference to existing users."""
    await _ensure_column(
        db_pool,
        "users",
        "exclude_watched_default",
        """
        ALTER TABLE users
        ADD COLUMN exclude_watched_default BOOLEAN NOT NULL DEFAULT TRUE
        AFTER oauth_sub
        """,
    )


async def ensure_users_theme_preference_column(db_pool) -> None:
    """Add the theme preference column to existing users."""
    await _ensure_column(
        db_pool,
        "users",
        "theme_preference",
        """
        ALTER TABLE users
        ADD COLUMN theme_preference VARCHAR(10) NULL
        """,
    )


async def ensure_users_default_filters_json_column(db_pool) -> None:
    """Add the default filter presets column to existing users."""
    await _ensure_column(
        db_pool,
        "users",
        "default_filters_json",
        """
        ALTER TABLE users
        ADD COLUMN default_filters_json JSON NULL
        """,
    )


async def ensure_users_exclude_watchlist_default_column(db_pool) -> None:
    """Add the default watchlist-exclusion preference to existing users."""
    await _ensure_column(
        db_pool,
        "users",
        "exclude_watchlist_default",
        """
        ALTER TABLE users
        ADD COLUMN exclude_watchlist_default BOOLEAN NOT NULL DEFAULT TRUE
        AFTER exclude_watched_default
        """,
    )


_SHUFFLE_KEY_BACKFILL_CHUNK_SIZE = 10000
_SHUFFLE_KEY_BACKFILL_LOCK_NAME = "nextreel_shuffle_key_backfill"
_SHUFFLE_KEY_BACKFILL_LOCK_TIMEOUT_SECONDS = 0


async def ensure_movie_candidates_shuffle_key(db_pool) -> None:
    """Add and backfill the additive shuffle key column.

    The UPDATE backfill and ALTER ... MODIFY tightening only need to run
    once per database. We gate them behind a ``shuffle_key_backfill_done``
    runtime_metadata flag so subsequent startups skip the work.

    Cross-replica safety: when multiple app replicas boot simultaneously
    on a fresh DB, a MySQL-side ``GET_LOCK`` prevents two replicas from
    racing the same large UPDATE. Non-acquiring replicas short-circuit
    and let the winning replica complete the backfill.

    The backfill itself is chunked (10k rows at a time) to cap undo-log
    growth, reduce InnoDB row-lock fanout, and keep replication lag
    bounded when the source table is large.
    """
    await _ensure_column(
        db_pool,
        "movie_candidates",
        "shuffle_key",
        """
        ALTER TABLE movie_candidates
        ADD COLUMN shuffle_key INT NULL AFTER sample_bucket
        """,
    )

    if await _get_runtime_flag(db_pool, "shuffle_key_backfill_done"):
        logger.debug("shuffle_key backfill already complete, skipping")
        return

    lock_row = await db_pool.execute(
        "SELECT GET_LOCK(%s, %s) AS locked",
        [_SHUFFLE_KEY_BACKFILL_LOCK_NAME, _SHUFFLE_KEY_BACKFILL_LOCK_TIMEOUT_SECONDS],
        fetch="one",
    )
    acquired = bool(lock_row and lock_row.get("locked") == 1) if isinstance(lock_row, dict) else False
    if not acquired:
        logger.info(
            "shuffle_key backfill lock held by another replica; skipping on this node"
        )
        return

    try:
        if await _get_runtime_flag(db_pool, "shuffle_key_backfill_done"):
            # Another replica finished while we were trying to acquire.
            logger.debug("shuffle_key backfill already complete, skipping")
            return

        total_updated = 0
        while True:
            affected = await db_pool.execute(
                """
                UPDATE movie_candidates
                SET shuffle_key = MOD(CAST(CRC32(tconst) AS UNSIGNED), 2147483647)
                WHERE shuffle_key IS NULL
                LIMIT %s
                """,
                [_SHUFFLE_KEY_BACKFILL_CHUNK_SIZE],
                fetch="none",
            )
            affected_count = affected if isinstance(affected, int) else 0
            total_updated += affected_count
            if affected_count < _SHUFFLE_KEY_BACKFILL_CHUNK_SIZE:
                break

        await db_pool.execute(
            """
            ALTER TABLE movie_candidates
            MODIFY COLUMN shuffle_key INT NOT NULL
            """,
            fetch="none",
        )
        await _set_runtime_flag(db_pool, "shuffle_key_backfill_done", "1")
        logger.info("shuffle_key backfill complete (%d rows updated)", total_updated)
    finally:
        try:
            await db_pool.execute(
                "SELECT RELEASE_LOCK(%s) AS released",
                [_SHUFFLE_KEY_BACKFILL_LOCK_NAME],
                fetch="one",
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "shuffle_key backfill RELEASE_LOCK failed (connection may have been reset)",
                exc_info=True,
            )


async def ensure_movie_candidates_refreshed_at_index(db_pool) -> None:
    """Ensure movie_candidates has a cheap freshest-row lookup."""
    await _ensure_index(
        db_pool,
        "movie_candidates",
        "idx_movie_candidates_refreshed_at",
        "CREATE INDEX idx_movie_candidates_refreshed_at ON movie_candidates (refreshed_at)",
    )


async def ensure_movie_candidates_shuffle_key_index(db_pool) -> None:
    """Ensure shuffle_key has an index to support the hot candidate-fetch sort.

    movies/candidate_store.py orders candidate queries by
    (shuffle_key, numVotes DESC, averageRating DESC). Without this index
    MySQL filesorts on every fetch.
    """
    await _ensure_index(
        db_pool,
        "movie_candidates",
        "idx_movie_candidates_shuffle",
        "CREATE INDEX idx_movie_candidates_shuffle "
        "ON movie_candidates (shuffle_key, numVotes, averageRating)",
    )


async def ensure_movie_candidates_primaryTitle_index(db_pool) -> None:
    """Add a 128-byte prefix index on primaryTitle to support /api/search.

    Without this, the search endpoint falls back to full table scans on
    every keystroke (debounced 150ms + rate-limited, but still expensive
    at scale). The prefix index covers equality and LIKE 'term%' — the
    LIKE '%term%' branch still scans, which is acceptable given the
    debounce + rate limits.
    """
    await _ensure_index(
        db_pool,
        table="movie_candidates",
        name="idx_movie_candidates_primaryTitle",
        create_sql=(
            "CREATE INDEX idx_movie_candidates_primaryTitle "
            "ON movie_candidates (primaryTitle(128))"
        ),
    )


async def ensure_movie_candidates_bucket_filter_index(db_pool) -> None:
    """Ensure the hot candidate filter can prune sample buckets before ranges.

    The candidate picker always constrains title type plus a small random
    subset of sample buckets, then applies numVotes/averageRating/startYear
    ranges. The legacy filter index places sample_bucket last, which forces
    full scans once range predicates are present. This composite order keeps
    the same query semantics while letting MySQL narrow the hot path to the
    selected buckets first.
    """
    await _ensure_index(
        db_pool,
        "movie_candidates",
        "idx_movie_candidates_bucket_filter",
        "CREATE INDEX idx_movie_candidates_bucket_filter "
        "ON movie_candidates (titleType, sample_bucket, numVotes, averageRating, startYear)",
    )


async def ensure_movie_candidates_fulltext_index(db_pool) -> None:
    """Repair the active movie_candidates FULLTEXT index when it is missing.

    Uses the shared ``_ensure_index`` helper so the duplicate-key errno
    (1061) is the canonical 'already exists' signal — removes the prior
    TOCTOU probe against ``information_schema.statistics``.
    """
    await _ensure_index(
        db_pool,
        "movie_candidates",
        _CANDIDATE_GENRE_FULLTEXT_INDEX,
        f"ALTER TABLE movie_candidates ADD FULLTEXT KEY {_CANDIDATE_GENRE_FULLTEXT_INDEX} (genres)",
    )


async def ensure_movie_projection_state_last_attempt_index(db_pool) -> None:
    """Ensure the FAILED-retry scan in requeue_stale_projections has an index.

    ``ProjectionRepository.requeue_stale_projections`` runs
    ``UPDATE ... WHERE projection_state = 'failed'
           AND (last_attempt_at IS NULL OR last_attempt_at <= %s) LIMIT N``
    in a loop. Without a composite index on (projection_state,
    last_attempt_at) MySQL can only use the state-only side of the
    existing ``idx_movie_projection_state_stale``, then filter every
    matched row by last_attempt_at — expensive when the failed bucket
    grows.
    """
    await _ensure_index(
        db_pool,
        "movie_projection",
        "idx_movie_projection_state_last_attempt",
        "CREATE INDEX idx_movie_projection_state_last_attempt "
        "ON movie_projection (projection_state, last_attempt_at)",
    )


# Order matches the historical orchestrator sequence — kept stable so add-column
# / add-index ordering remains predictable. Append new helper names to the end.
# Names (not function refs) so test patches against the module attribute apply.
_RUNTIME_REPAIR_HELPER_NAMES = (
    "ensure_user_navigation_current_ref_column",
    "ensure_movie_candidates_shuffle_key",
    "ensure_movie_candidates_refreshed_at_index",
    "ensure_movie_candidates_shuffle_key_index",
    "ensure_movie_candidates_bucket_filter_index",
    "ensure_movie_candidates_primaryTitle_index",
    "ensure_movie_candidates_fulltext_index",
    "ensure_movie_projection_state_last_attempt_index",
    "ensure_user_navigation_user_id_column",
    "ensure_users_exclude_watched_default_column",
    "ensure_users_theme_preference_column",
    "ensure_users_default_filters_json_column",
    "ensure_users_exclude_watchlist_default_column",
)
