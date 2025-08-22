#!/bin/bash

# IMDB Database Index Maintenance Script
# Run this periodically to maintain optimal query performance

DB_HOST="localhost"
DB_USER="root"
DB_PASS="caching_sha2_password"
DB_NAME="imdb"

echo "=========================================="
echo "IMDB Database Index Maintenance"
echo "=========================================="
echo ""

# Function to run MySQL commands
run_mysql() {
    /usr/local/mysql/bin/mysql -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "$1" 2>/dev/null
}

# 1. Check table statistics
echo "1. Checking table statistics..."
run_mysql "
SELECT 
    TABLE_NAME,
    TABLE_ROWS,
    ROUND(DATA_LENGTH/1024/1024/1024, 2) as DATA_GB,
    ROUND(INDEX_LENGTH/1024/1024/1024, 2) as INDEX_GB,
    ROUND((DATA_LENGTH + INDEX_LENGTH)/1024/1024/1024, 2) as TOTAL_GB
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'imdb'
ORDER BY TABLE_ROWS DESC;"

echo ""
echo "2. Updating table statistics..."
run_mysql "ANALYZE TABLE title_basics, name_basics, title_crew, title_episode, title_ratings;"

# Check if the large tables have finished loading
AKAS_COUNT=$(run_mysql "SELECT COUNT(*) FROM title_akas;" | tail -1)
PRINCIPALS_COUNT=$(run_mysql "SELECT COUNT(*) FROM title_principals;" | tail -1)

if [ "$AKAS_COUNT" -gt 0 ]; then
    echo "   Analyzing title_akas (${AKAS_COUNT} rows)..."
    run_mysql "ANALYZE TABLE title_akas;"
fi

if [ "$PRINCIPALS_COUNT" -gt 0 ]; then
    echo "   Analyzing title_principals (${PRINCIPALS_COUNT} rows)..."
    run_mysql "ANALYZE TABLE title_principals;"
fi

echo ""
echo "3. Index statistics..."
run_mysql "
SELECT 
    TABLE_NAME,
    COUNT(DISTINCT INDEX_NAME) as INDEX_COUNT,
    GROUP_CONCAT(INDEX_NAME SEPARATOR ', ') as INDEXES
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'imdb'
GROUP BY TABLE_NAME
ORDER BY TABLE_NAME;"

echo ""
echo "4. Checking for missing primary keys..."
run_mysql "
SELECT TABLE_NAME 
FROM information_schema.TABLES t
WHERE TABLE_SCHEMA = 'imdb'
AND NOT EXISTS (
    SELECT 1 
    FROM information_schema.STATISTICS s
    WHERE s.TABLE_SCHEMA = t.TABLE_SCHEMA
    AND s.TABLE_NAME = t.TABLE_NAME
    AND s.INDEX_NAME = 'PRIMARY'
);"

echo ""
echo "=========================================="
echo "Maintenance complete!"
echo "=========================================="
echo ""
echo "Recommendations:"
echo "1. Run this script weekly or after large data imports"
echo "2. Monitor slow query log for additional indexing needs"
echo "3. Consider partitioning for tables > 50GB"
echo "4. Use EXPLAIN on slow queries to verify index usage"