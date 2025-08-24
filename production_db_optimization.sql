-- =====================================================
-- NextReel Production Database Optimization
-- Safe Execution Script for DigitalOcean MySQL
-- =====================================================

USE imdb;

-- =====================================================
-- PHASE 1: ADD PRIMARY KEYS (if not exists)
-- =====================================================

-- Check existing primary keys
SELECT TABLE_NAME, CONSTRAINT_NAME 
FROM information_schema.TABLE_CONSTRAINTS 
WHERE CONSTRAINT_TYPE = 'PRIMARY KEY' 
AND TABLE_SCHEMA = 'imdb';

-- Add primary keys with IF NOT EXISTS logic
ALTER TABLE `title.basics` ADD PRIMARY KEY IF NOT EXISTS (tconst);
ALTER TABLE `title.ratings` ADD PRIMARY KEY IF NOT EXISTS (tconst);
ALTER TABLE `title.crew` ADD PRIMARY KEY IF NOT EXISTS (tconst);
ALTER TABLE `title.episode` ADD PRIMARY KEY IF NOT EXISTS (tconst);
ALTER TABLE `name.basics` ADD PRIMARY KEY IF NOT EXISTS (nconst);

-- =====================================================
-- PHASE 2: HIGH-IMPACT INDEXES FOR FILTER QUERIES
-- =====================================================

-- Drop existing indexes if they exist (to avoid duplicates)
ALTER TABLE `title.basics` DROP INDEX IF EXISTS idx_basics_compound;
ALTER TABLE `title.basics` DROP INDEX IF EXISTS idx_startYear;
ALTER TABLE `title.basics` DROP INDEX IF EXISTS idx_titleType;
ALTER TABLE `title.ratings` DROP INDEX IF EXISTS idx_ratings_compound;
ALTER TABLE `title.ratings` DROP INDEX IF EXISTS idx_averagerating;
ALTER TABLE `title.ratings` DROP INDEX IF EXISTS idx_numVotes;

-- Create optimized compound index for main filter query
ALTER TABLE `title.basics` ADD INDEX idx_basics_compound (
    titleType,
    startYear,
    language(20)
);

-- Individual indexes for flexible filtering
ALTER TABLE `title.basics` ADD INDEX idx_startYear (startYear);
ALTER TABLE `title.basics` ADD INDEX idx_titleType (titleType);

-- Optimized rating indexes
ALTER TABLE `title.ratings` ADD INDEX idx_ratings_compound (
    numVotes,
    averageRating
);

ALTER TABLE `title.ratings` ADD INDEX idx_averagerating (averageRating);
ALTER TABLE `title.ratings` ADD INDEX idx_numVotes (numVotes);

-- =====================================================
-- PHASE 3: FULLTEXT INDEXES FOR SEARCH
-- =====================================================

-- Drop existing fulltext indexes if they exist
ALTER TABLE `title.basics` DROP INDEX IF EXISTS idx_genres_ft;
ALTER TABLE `title.basics` DROP INDEX IF EXISTS idx_title_ft;

-- Create FULLTEXT indexes
ALTER TABLE `title.basics` ADD FULLTEXT idx_genres_ft (genres);
ALTER TABLE `title.basics` ADD FULLTEXT idx_title_ft (primaryTitle, originalTitle);

-- =====================================================
-- PHASE 4: OPTIMIZED CACHE TABLE
-- =====================================================

DROP TABLE IF EXISTS popular_movies_cache;

CREATE TABLE popular_movies_cache (
    tconst VARCHAR(10) PRIMARY KEY,
    titleType VARCHAR(50),
    primaryTitle TEXT,
    originalTitle TEXT,
    isAdult TINYINT,
    startYear INT,
    endYear INT,
    runtimeMinutes INT,
    genres TEXT,
    language TEXT,
    slug VARCHAR(255),
    plot TEXT,
    poster_url TEXT,
    averageRating DECIMAL(3,1),
    numVotes INT,
    rand_order DOUBLE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cache_filter (startYear, numVotes, averageRating),
    INDEX idx_cache_rand (rand_order),
    INDEX idx_cache_lang (language(20)),
    FULLTEXT idx_cache_genres (genres)
) ENGINE=InnoDB;

-- Populate cache with popular movies
INSERT INTO popular_movies_cache
SELECT 
    tb.tconst,
    tb.titleType,
    tb.primaryTitle,
    tb.originalTitle,
    tb.isAdult,
    tb.startYear,
    tb.endYear,
    tb.runtimeMinutes,
    tb.genres,
    tb.language,
    tb.slug,
    tb.plot,
    tb.poster_url,
    tr.averageRating,
    tr.numVotes,
    RAND() as rand_order,
    NOW() as created_at
FROM `title.basics` tb
INNER JOIN `title.ratings` tr ON tb.tconst = tr.tconst
WHERE tb.titleType = 'movie'
AND tb.startYear >= 1980
AND tr.numVotes >= 10000
AND tr.averageRating >= 5.0
LIMIT 50000;  -- Limit to top 50k movies for performance

-- =====================================================
-- PHASE 5: RECENT MOVIES CACHE (2024-2025)
-- =====================================================

DROP TABLE IF EXISTS recent_movies_cache;

CREATE TABLE recent_movies_cache (
    tconst VARCHAR(10) PRIMARY KEY,
    primaryTitle TEXT,
    startYear INT,
    genres TEXT,
    language TEXT,
    titleType VARCHAR(50),
    slug VARCHAR(255),
    plot TEXT,
    poster_url TEXT,
    averageRating DECIMAL(3,1),
    numVotes INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_recent_year (startYear),
    INDEX idx_recent_filter (startYear, numVotes, averageRating),
    FULLTEXT idx_recent_genres (genres)
) ENGINE=InnoDB;

INSERT INTO recent_movies_cache
SELECT 
    tb.tconst,
    tb.primaryTitle,
    tb.startYear,
    tb.genres,
    tb.language,
    tb.titleType,
    tb.slug,
    tb.plot,
    tb.poster_url,
    COALESCE(tr.averageRating, 0) as averageRating,
    COALESCE(tr.numVotes, 0) as numVotes,
    NOW() as created_at
FROM `title.basics` tb
LEFT JOIN `title.ratings` tr ON tb.tconst = tr.tconst
WHERE tb.titleType = 'movie'
AND tb.startYear >= 2024;

-- =====================================================
-- PHASE 6: ADDITIONAL INDEXES
-- =====================================================

-- Optimize other tables
ALTER TABLE `title.akas` DROP INDEX IF EXISTS idx_title_akas_titleId;
ALTER TABLE `title.akas` ADD INDEX idx_title_akas_titleId (titleId);

ALTER TABLE `title.principals` DROP INDEX IF EXISTS idx_tconst_nconst;
ALTER TABLE `title.principals` DROP INDEX IF EXISTS idx_nconst;
ALTER TABLE `title.principals` ADD INDEX idx_tconst_nconst (tconst(10), nconst(10));
ALTER TABLE `title.principals` ADD INDEX idx_nconst (nconst(10));

ALTER TABLE `title.crew` DROP INDEX IF EXISTS idx_title_crew_tconst;
ALTER TABLE `title.crew` ADD INDEX idx_title_crew_tconst (tconst);

ALTER TABLE `title.episode` DROP INDEX IF EXISTS idx_title_episode_parentTconst;
ALTER TABLE `title.episode` ADD INDEX idx_title_episode_parentTconst (parentTconst);

-- =====================================================
-- PHASE 7: UPDATE STATISTICS
-- =====================================================

ANALYZE TABLE `title.basics`;
ANALYZE TABLE `title.ratings`;
ANALYZE TABLE `title.akas`;
ANALYZE TABLE `title.principals`;
ANALYZE TABLE `title.crew`;
ANALYZE TABLE `title.episode`;
ANALYZE TABLE `name.basics`;
ANALYZE TABLE popular_movies_cache;
ANALYZE TABLE recent_movies_cache;

-- =====================================================
-- PHASE 8: CREATE MAINTENANCE PROCEDURE
-- =====================================================

DROP PROCEDURE IF EXISTS refresh_movie_caches;

DELIMITER $$
CREATE PROCEDURE refresh_movie_caches()
BEGIN
    -- Refresh popular movies cache
    TRUNCATE TABLE popular_movies_cache;
    
    INSERT INTO popular_movies_cache
    SELECT 
        tb.tconst,
        tb.titleType,
        tb.primaryTitle,
        tb.originalTitle,
        tb.isAdult,
        tb.startYear,
        tb.endYear,
        tb.runtimeMinutes,
        tb.genres,
        tb.language,
        tb.slug,
        tb.plot,
        tb.poster_url,
        tr.averageRating,
        tr.numVotes,
        RAND() as rand_order,
        NOW() as created_at
    FROM `title.basics` tb
    INNER JOIN `title.ratings` tr ON tb.tconst = tr.tconst
    WHERE tb.titleType = 'movie'
    AND tb.startYear >= 1980
    AND tr.numVotes >= 10000
    AND tr.averageRating >= 5.0
    LIMIT 50000;
    
    -- Refresh recent movies cache
    TRUNCATE TABLE recent_movies_cache;
    
    INSERT INTO recent_movies_cache
    SELECT 
        tb.tconst,
        tb.primaryTitle,
        tb.startYear,
        tb.genres,
        tb.language,
        tb.titleType,
        tb.slug,
        tb.plot,
        tb.poster_url,
        COALESCE(tr.averageRating, 0) as averageRating,
        COALESCE(tr.numVotes, 0) as numVotes,
        NOW() as created_at
    FROM `title.basics` tb
    LEFT JOIN `title.ratings` tr ON tb.tconst = tr.tconst
    WHERE tb.titleType = 'movie'
    AND tb.startYear >= 2024;
    
    -- Update statistics
    ANALYZE TABLE popular_movies_cache;
    ANALYZE TABLE recent_movies_cache;
END$$
DELIMITER ;

-- =====================================================
-- PHASE 9: VERIFY INDEXES
-- =====================================================

-- Check all indexes
SELECT 
    TABLE_NAME,
    INDEX_NAME,
    GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) as COLUMNS
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'imdb'
GROUP BY TABLE_NAME, INDEX_NAME
ORDER BY TABLE_NAME, INDEX_NAME;

-- Test query performance
EXPLAIN SELECT SQL_NO_CACHE tb.* 
FROM `title.basics` tb 
JOIN `title.ratings` tr ON tb.tconst = tr.tconst
WHERE tb.titleType = 'movie'
AND tb.startYear BETWEEN 2000 AND 2023
AND tr.averageRating >= 7.0
AND tr.numVotes >= 100000
LIMIT 15;

-- =====================================================
-- PERFORMANCE MONITORING QUERIES
-- =====================================================

-- Monitor slow queries
SELECT * FROM mysql.slow_log ORDER BY start_time DESC LIMIT 10;

-- Check index usage
SELECT 
    object_schema,
    object_name,
    index_name,
    count_star AS rows_examined,
    sum_timer_wait/1000000000000 AS total_time_sec
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE object_schema = 'imdb'
ORDER BY sum_timer_wait DESC
LIMIT 20;