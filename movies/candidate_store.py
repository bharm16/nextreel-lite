"""Precomputed candidate-table selection and refresh helpers."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from infra.navigation_state import criteria_from_filters
from infra.time_utils import utcnow
from logging_config import get_logger
from movies.query_builder import MovieQueryBuilder

logger = get_logger(__name__)

SAMPLE_BUCKET_COUNT = 128
SELECTION_BUCKET_STEPS = (2, 8, 32, SAMPLE_BUCKET_COUNT)
_MYSQL_INT_MAX = 2_147_483_647

_ALLOWED_CANDIDATE_TABLES = frozenset({"movie_candidates_next", "movie_candidates"})


def _ref_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Build a lightweight movie ref dict from a DB row."""
    return {
        "tconst": row["tconst"],
        "title": row.get("primaryTitle") or "Unknown",
        "slug": row.get("slug"),
    }


class CandidateStore:
    def __init__(self, db_pool):
        self.db_pool = db_pool
        self._table_maintainer: CandidateTableMaintainer | None = None

    async def latest_refresh_at(self) -> datetime | None:
        row = await self.db_pool.execute(
            "SELECT MAX(refreshed_at) AS refreshed_at FROM movie_candidates",
            fetch="one",
        )
        return row["refreshed_at"] if row else None

    async def has_fresh_data(self, max_age_hours: int = 24) -> bool:
        refreshed_at = await self.latest_refresh_at()
        if not refreshed_at:
            return False
        age = utcnow() - refreshed_at
        return age.total_seconds() <= max_age_hours * 3600

    async def fetch_ref(self, tconst: str) -> dict[str, Any] | None:
        row = await self.db_pool.execute(
            """
            (SELECT tconst, primaryTitle, slug FROM movie_candidates WHERE tconst = %s)
            UNION ALL
            (SELECT tconst, primaryTitle, slug FROM `title.basics` WHERE tconst = %s)
            LIMIT 1
            """,
            [tconst, tconst],
            fetch="one",
        )
        if not row:
            return None
        return _ref_from_row(row)

    def _genre_clause(self, criteria: dict[str, Any]) -> tuple[str, list[Any]]:
        return MovieQueryBuilder.build_genre_conditions_fulltext(criteria, use_cache=True)

    async def fetch_candidate_refs(
        self,
        filters: dict[str, Any],
        excluded_tconsts: set[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        criteria = criteria_from_filters(filters)
        desired_limit = max(1, limit)
        seed = str(random.randint(0, _MYSQL_INT_MAX))

        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", utcnow().year)
        min_rating = criteria.get("min_rating", 0)
        max_rating = criteria.get("max_rating", 10)
        min_votes = criteria.get("min_votes", 100000)
        max_votes = criteria.get("max_votes", 1000000)
        language = criteria.get("language", "en")

        for bucket_count in SELECTION_BUCKET_STEPS:
            buckets = random.sample(range(SAMPLE_BUCKET_COUNT), bucket_count)
            params: list[Any] = [
                "movie",
                min_year,
                max_year,
                min_rating,
                max_rating,
                min_votes,
                max_votes,
                language,
                language,
                f"%{language}%",
            ]
            clauses = [
                "titleType = %s",
                "startYear BETWEEN %s AND %s",
                "averageRating BETWEEN %s AND %s",
                "numVotes BETWEEN %s AND %s",
                "(%s = 'any' OR language = %s OR language LIKE %s OR language IS NULL)",
                f"sample_bucket IN ({', '.join(['%s'] * len(buckets))})",
            ]
            params.extend(buckets)

            if excluded_tconsts:
                clauses.append(f"tconst NOT IN ({', '.join(['%s'] * len(excluded_tconsts))})")
                params.extend(sorted(excluded_tconsts))

            genre_clause, genre_params = self._genre_clause(criteria)
            params.extend(genre_params)

            query = f"""
                SELECT tconst, primaryTitle, slug
                FROM movie_candidates
                WHERE {' AND '.join(clauses)}{genre_clause}
                ORDER BY MOD(CRC32(CONCAT(tconst, %s)), {_MYSQL_INT_MAX}), numVotes DESC, averageRating DESC
                LIMIT %s
            """
            params.extend([seed, desired_limit * 3])

            rows = await self.db_pool.execute(query, params, fetch="all")
            if rows:
                deduped: list[dict[str, Any]] = []
                seen: set[str] = set()
                for row in rows:
                    tconst = row["tconst"]
                    if tconst in seen:
                        continue
                    seen.add(tconst)
                    deduped.append(_ref_from_row(row))
                    if len(deduped) >= desired_limit:
                        break
                return deduped

        return []

    async def validate_bucket_distribution(self, table_name: str = "movie_candidates_next") -> None:
        return await self._maintainer.validate_bucket_distribution(table_name)

    async def refresh_movie_candidates(self) -> None:
        return await self._maintainer.refresh_movie_candidates()

    @property
    def _maintainer(self):
        if self._table_maintainer is None:
            self._table_maintainer = CandidateTableMaintainer(self.db_pool)
        return self._table_maintainer


class CandidateTableMaintainer:
    """DDL and validation operations for the movie_candidates table.

    Extracted from CandidateStore to separate read-path (candidate selection)
    from write-path (table refresh/validation).
    """

    def __init__(self, db_pool):
        self.db_pool = db_pool

    async def validate_bucket_distribution(self, table_name: str = "movie_candidates_next") -> None:
        if table_name not in _ALLOWED_CANDIDATE_TABLES:
            raise ValueError(f"Invalid candidate table name: {table_name}")
        rows = await self.db_pool.execute(
            f"""
            SELECT sample_bucket, COUNT(*) AS bucket_count
            FROM {table_name}
            GROUP BY sample_bucket
            ORDER BY sample_bucket
            """,
            fetch="all",
        )
        if not rows:
            raise RuntimeError(f"{table_name} refresh produced no rows")

        counts = [0] * SAMPLE_BUCKET_COUNT
        for row in rows:
            counts[int(row["sample_bucket"])] = row["bucket_count"]
        mean = sum(counts) / SAMPLE_BUCKET_COUNT
        lower = mean * 0.75
        upper = mean * 1.25
        for count in counts:
            if count < lower or count > upper:
                raise RuntimeError(
                    f"{table_name} bucket distribution skew detected: {count} outside {lower:.2f}-{upper:.2f}"
                )

    async def refresh_movie_candidates(self) -> None:
        logger.info("Refreshing movie_candidates")
        await self.db_pool.execute("DROP TABLE IF EXISTS movie_candidates_next", fetch="none")
        await self.db_pool.execute(
            """
            CREATE TABLE movie_candidates_next (
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
                refreshed_at DATETIME(6) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            fetch="none",
        )
        await self.db_pool.execute(
            """
            INSERT INTO movie_candidates_next (
                tconst, primaryTitle, startYear, genres, language, titleType, slug,
                averageRating, numVotes, sample_bucket, refreshed_at
            )
            SELECT
                tb.tconst,
                tb.primaryTitle,
                tb.startYear,
                tb.genres,
                tb.language,
                tb.titleType,
                tb.slug,
                COALESCE(tr.averageRating, 0),
                COALESCE(tr.numVotes, 0),
                MOD(CRC32(tb.tconst), 128),
                UTC_TIMESTAMP(6)
            FROM `title.basics` tb
            LEFT JOIN `title.ratings` tr ON tb.tconst = tr.tconst
            WHERE tb.titleType = 'movie' AND tb.startYear IS NOT NULL
            """,
            fetch="none",
        )
        for statement in (
            "CREATE INDEX idx_movie_candidates_next_filter ON movie_candidates_next (titleType, startYear, averageRating, numVotes, sample_bucket)",
            "CREATE INDEX idx_movie_candidates_next_language ON movie_candidates_next (language)",
            "CREATE INDEX idx_movie_candidates_next_slug ON movie_candidates_next (slug(191))",
            "CREATE FULLTEXT INDEX ftx_movie_candidates_next_genres ON movie_candidates_next (genres)",
        ):
            await self.db_pool.execute(statement, fetch="none")

        await self.validate_bucket_distribution("movie_candidates_next")
        await self.db_pool.execute("DROP TABLE IF EXISTS movie_candidates_prev", fetch="none")
        await self.db_pool.execute(
            """
            RENAME TABLE
                movie_candidates TO movie_candidates_prev,
                movie_candidates_next TO movie_candidates
            """,
            fetch="none",
        )
        row = await self.db_pool.execute(
            "SELECT COUNT(*) AS total FROM movie_candidates",
            fetch="one",
        )
        if not row or row["total"] <= 0:
            raise RuntimeError("movie_candidates swap produced an empty active table")
        await self.db_pool.execute("DROP TABLE IF EXISTS movie_candidates_prev", fetch="none")
        logger.info("movie_candidates refresh complete")
