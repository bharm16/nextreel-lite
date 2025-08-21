-- NextReel-Lite Project Optimized Indexes
-- Based on actual queries found in the application code

USE imdb;

-- =====================================================
-- CRITICAL INDEXES FOR YOUR APPLICATION QUERIES
-- =====================================================

-- 1. Main movie filtering query (filter_backend.py lines 30-38)
-- Query: SELECT tb.* FROM title.basics tb 
--        JOIN title.ratings tr ON tb.tconst = tr.tconst
--        WHERE tb.startYear BETWEEN %s AND %s
--        AND tr.averagerating BETWEEN %s AND %s
--        AND tr.numVotes >= %s AND tr.numVotes <= %s
--        AND tb.titleType = %s
--        AND tb.language LIKE %s
--        AND tb.genres LIKE %s (optional)
--        ORDER BY RAND() LIMIT 15

-- Primary key for join efficiency
ALTER TABLE `title.basics` ADD PRIMARY KEY (tconst);
ALTER TABLE `title.ratings` ADD PRIMARY KEY (tconst);

-- Composite index for the main filtering query
ALTER TABLE `title.basics` ADD INDEX idx_filter_query (
    titleType, 
    startYear, 
    language(10),
    tconst
);

-- Index for year range queries
ALTER TABLE `title.basics` ADD INDEX idx_year_range (startYear, endYear);

-- Index for ratings table filtering
ALTER TABLE `title.ratings` ADD INDEX idx_rating_filter (
    averageRating,
    numVotes,
    tconst
);

-- Index for vote range queries (common filter)
ALTER TABLE `title.ratings` ADD INDEX idx_votes_range (numVotes, averageRating);

-- Full-text index for genre searches (for LIKE '%genre%' queries)
ALTER TABLE `title.basics` ADD FULLTEXT idx_genres_fulltext (genres);

-- Index for language filtering
ALTER TABLE `title.basics` ADD INDEX idx_language (language);

-- 2. Slug lookup query (movie.py lines 102-104)
-- Query: SELECT slug FROM title.basics WHERE tconst = %s
ALTER TABLE `title.basics` ADD INDEX idx_slug_lookup (tconst, slug);

-- 3. Ratings lookup query (movie.py lines 24-28)
-- Query: SELECT tr.tconst, tr.averageRating, tr.numVotes 
--        FROM title.ratings tr WHERE tr.tconst = %s
-- Already covered by primary key on tconst

-- 4. Optimize JOIN performance between title.basics and title.ratings
-- This is critical for your main query performance
ALTER TABLE `title.ratings` ADD INDEX idx_join_tconst (tconst, averageRating, numVotes);

-- 5. Covering index for the complete filter query
-- This allows the database to satisfy the query entirely from the index
ALTER TABLE `title.basics` ADD INDEX idx_covering_filter (
    titleType,
    startYear,
    tconst,
    genres(50),
    language(10),
    slug,
    primaryTitle
);

-- =====================================================
-- PERFORMANCE OPTIMIZATION FOR RANDOM SELECTION
-- =====================================================

-- Since you use ORDER BY RAND() frequently, consider these optimizations:

-- Create a smaller subset table for active movies (optional but recommended)
-- This dramatically improves RAND() performance
CREATE TABLE IF NOT EXISTS title_basics_active AS
SELECT tb.*, tr.averageRating, tr.numVotes
FROM `title.basics` tb
JOIN `title.ratings` tr ON tb.tconst = tr.tconst
WHERE tb.titleType = 'movie'
AND tb.startYear >= 1990
AND tr.numVotes >= 10000
AND tr.averageRating >= 5.0;

-- Index the active movies table
ALTER TABLE title_basics_active ADD PRIMARY KEY (tconst);
ALTER TABLE title_basics_active ADD INDEX idx_active_filter (
    startYear,
    averageRating,
    numVotes,
    genres(50),
    language(10)
);

-- =====================================================
-- PERFORMANCE FIXES FOR SLOW QUERIES
-- =====================================================

-- Based on performance testing results:
-- Genre queries taking 41+ seconds need optimization

-- 1. Add FULLTEXT indexes for genre and title searches
ALTER TABLE `title.basics` ADD FULLTEXT idx_genres_ft (genres);
ALTER TABLE `title.basics` ADD FULLTEXT idx_title_ft (primaryTitle, originalTitle);

-- 2. Create materialized view/cache table for faster random selection
-- This dramatically improves ORDER BY RAND() performance
DROP TABLE IF EXISTS popular_movies_cache;

CREATE TABLE popular_movies_cache AS
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
    tr.averageRating,
    tr.numVotes,
    RAND() as rand_order
FROM `title.basics` tb
INNER JOIN `title.ratings` tr ON tb.tconst = tr.tconst
WHERE tb.titleType = 'movie'
AND tb.startYear >= 1980
AND tr.numVotes >= 10000
AND tr.averageRating >= 5.0;

-- Index the cache table
ALTER TABLE popular_movies_cache ADD PRIMARY KEY (tconst);
ALTER TABLE popular_movies_cache ADD INDEX idx_cache_filter (
    startYear,
    averageRating,
    numVotes,
    rand_order
);
ALTER TABLE popular_movies_cache ADD FULLTEXT idx_cache_genres (genres);
ALTER TABLE popular_movies_cache ADD INDEX idx_cache_lang (language(20));

-- 3. Add better composite indexes for JOIN operations
ALTER TABLE `title.ratings` ADD INDEX idx_rating_join (tconst, averageRating, numVotes);
ALTER TABLE `title.basics` ADD INDEX idx_lang (language(10));

-- =====================================================
-- OPTIMIZATION FOR 2024-2025 DATA (NEW)
-- =====================================================

-- The recent data update added many 2024-2025 movies causing timeouts
-- Create specific index for recent years with ratings

-- Composite index optimized for recent year queries
ALTER TABLE `title.basics` ADD INDEX idx_recent_years (
    startYear DESC,
    titleType,
    tconst
);

-- Create a fast lookup table for 2024-2025 movies
DROP TABLE IF EXISTS recent_movies_cache;
CREATE TABLE recent_movies_cache AS
SELECT 
    tb.tconst,
    tb.primaryTitle,
    tb.startYear,
    tb.genres,
    tb.language,
    tb.titleType,
    tr.averageRating,
    tr.numVotes
FROM `title.basics` tb
LEFT JOIN `title.ratings` tr ON tb.tconst = tr.tconst
WHERE tb.titleType = 'movie'
AND tb.startYear >= 2024;

-- Index the recent movies cache
ALTER TABLE recent_movies_cache ADD PRIMARY KEY (tconst);
ALTER TABLE recent_movies_cache ADD INDEX idx_recent_filter (
    startYear,
    averageRating,
    numVotes
);

-- =====================================================
-- MONITORING AND MAINTENANCE
-- =====================================================

-- Update statistics for query optimizer
ANALYZE TABLE `title.basics`;
ANALYZE TABLE `title.ratings`;
ANALYZE TABLE recent_movies_cache;
ANALYZE TABLE `title.episode`;
ANALYZE TABLE `title.crew`;
ANALYZE TABLE `name.basics`;

-- Check index usage statistics
SELECT 
    TABLE_NAME,
    INDEX_NAME,
    CARDINALITY,
    SEQ_IN_INDEX,
    COLUMN_NAME
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'imdb'
AND TABLE_NAME IN ('title.basics', 'title.ratings')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;

-- =====================================================
-- QUERY PERFORMANCE VERIFICATION
-- =====================================================

-- Test the main filter query performance
EXPLAIN SELECT tb.* 
FROM `title.basics` tb 
JOIN `title.ratings` tr ON tb.tconst = tr.tconst
WHERE tb.startYear BETWEEN 2000 AND 2023
AND tr.averageRating BETWEEN 7.0 AND 10
AND tr.numVotes >= 100000 AND tr.numVotes <= 1000000
AND tb.titleType = 'movie'
AND tb.language LIKE '%en%'
AND tb.genres LIKE '%Action%'
ORDER BY RAND() 
LIMIT 15;

-- Monitor slow queries (enable if not already enabled)
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;
SET GLOBAL log_queries_not_using_indexes = 'ON';