"""Precomputed candidate-table selection and refresh helpers."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from nextreel.domain.filter_contracts import FilterState, MovieCriteria
from infra.errors import DatabaseError
from infra.filter_normalizer import criteria_from_filters
from infra.time_utils import utcnow
from logging_config import get_logger
from movies.candidate_filter_pool_cache import CandidateFilterPoolCache
from movies.query_builder import MovieQueryBuilder, is_fulltext_index_error

logger = get_logger(__name__)

SAMPLE_BUCKET_COUNT = 128
SELECTION_BUCKET_STEPS = (2, 8, 32, SAMPLE_BUCKET_COUNT)
_MYSQL_INT_MAX = 2147483647
_OVERFETCH_FACTOR = 3

_ALLOWED_CANDIDATE_TABLES = frozenset({"movie_candidates_next", "movie_candidates"})

# Filter-result cache: collapses repeated queries against the same filter
# combo (e.g., bots hitting unique filter pages) to one DB fetch per window.
# We cache a larger pool than any single caller needs, then sample from it,
# so concurrent callers still see varied results.
_FILTER_RESULT_POOL_SIZE = 50


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
        # Optional Redis cache wired by MovieManager.attach_cache. Used for
        # filter-result pooling so repeated unique-filter queries don't
        # each run a shuffle_key sort.
        self._cache = None
        self._filter_pool_cache = CandidateFilterPoolCache()

    def attach_cache(self, cache) -> None:
        """Attach a cache manager. Idempotent — callers may rebind."""
        self._cache = cache
        self._filter_pool_cache.attach_cache(cache)

    async def latest_refresh_at(self) -> datetime | None:
        row = await self.db_pool.execute(
            """
            SELECT refreshed_at
            FROM movie_candidates
            ORDER BY refreshed_at DESC
            LIMIT 1
            """,
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

    async def fetch_refs(self, tconsts: list[str]) -> list[dict[str, Any]]:
        """Batched variant of :meth:`fetch_ref` — one SELECT per call.

        Returns ref dicts for any tconsts present in either
        ``movie_candidates`` or ``title.basics``. Order is NOT guaranteed
        to match input order; missing tconsts are simply absent. The
        ``movie_candidates`` row wins on duplicates (matches the UNION ALL
        precedence in :meth:`fetch_ref`).
        """
        if not tconsts:
            return []
        # Deduplicate while preserving caller intent — repeated tconsts
        # in the input collapse to a single SELECT param.
        unique = list(dict.fromkeys(tconsts))
        placeholders = ",".join(["%s"] * len(unique))
        # Drop the NOT IN subquery the previous implementation used to keep
        # title.basics rows out when movie_candidates already had them. The
        # subquery scanned movie_candidates twice (once per leg). We instead
        # tag each leg with a source_priority, sort by it, and let the
        # Python-side `seen` dedup below keep the first occurrence —
        # movie_candidates wins because its priority is 0.
        sql = f"""
            SELECT tconst, primaryTitle, slug, 0 AS source_priority
            FROM movie_candidates
            WHERE tconst IN ({placeholders})
            UNION ALL
            SELECT tconst, primaryTitle, slug, 1 AS source_priority
            FROM `title.basics`
            WHERE tconst IN ({placeholders})
            ORDER BY source_priority
        """
        params = unique + unique
        rows = await self.db_pool.execute(sql, params, fetch="all")
        if not rows:
            return []
        seen: set[str] = set()
        refs: list[dict[str, Any]] = []
        for row in rows:
            tconst = row["tconst"]
            if tconst in seen:
                continue
            seen.add(tconst)
            refs.append(_ref_from_row(row))
        return refs

    def _genre_clause(
        self,
        criteria: dict[str, Any],
        *,
        use_fulltext: bool = True,
    ) -> tuple[str, list[Any]]:
        """Genre clause built against the candidate-table column set."""
        return MovieQueryBuilder.genre_clause(criteria, use_fulltext=use_fulltext, use_cache=True)

    def _build_candidate_query(
        self,
        *,
        criteria: dict[str, Any],
        excluded_tconsts: set[str],
        desired_limit: int,
        buckets: list[int],
        use_fulltext: bool,
    ) -> tuple[str, list[Any]]:
        min_year = criteria.get("min_year", 1900)
        max_year = criteria.get("max_year", utcnow().year)
        min_rating = criteria.get("min_rating", 0)
        max_rating = criteria.get("max_rating", 10)
        min_votes = criteria.get("min_votes", 100000)
        max_votes = criteria.get("max_votes", 1000000)
        language = criteria.get("language", "en")

        params: list[Any] = [
            "movie",
            min_year,
            max_year,
            min_rating,
            max_rating,
            min_votes,
            max_votes,
        ]
        clauses = [
            "titleType = %s",
            "startYear BETWEEN %s AND %s",
            "averageRating BETWEEN %s AND %s",
            "numVotes BETWEEN %s AND %s",
            f"sample_bucket IN ({', '.join(['%s'] * len(buckets))})",
        ]
        params.extend(buckets)
        if language != "any":
            clauses.append("(language = %s OR language LIKE %s OR language IS NULL)")
            params.extend([language, f"%{language}%"])

        if excluded_tconsts:
            clauses.append(f"tconst NOT IN ({', '.join(['%s'] * len(excluded_tconsts))})")
            params.extend(sorted(excluded_tconsts))

        genre_clause, genre_params = self._genre_clause(criteria, use_fulltext=use_fulltext)
        params.extend(genre_params)

        # ORDER BY uses only (shuffle_key, tconst). The old tail of
        # (numVotes DESC, averageRating DESC) was a tiebreaker for
        # shuffle_key collisions, but shuffle_key is ~uniform over
        # 0..2^31-1 on a ~200k row table so real collisions are vanishingly
        # rare. Dropping the DESC tail lets the ASC-only composite
        # idx_movie_candidates_shuffle (shuffle_key, numVotes, averageRating)
        # serve the sort without a filesort. tconst as the secondary
        # sort is the table's PK, giving deterministic ordering on ties.
        query = f"""
            SELECT tconst, primaryTitle, slug
            FROM movie_candidates
            WHERE {' AND '.join(clauses)}{genre_clause}
            ORDER BY shuffle_key, tconst
            LIMIT %s
        """
        params.append(desired_limit * _OVERFETCH_FACTOR)
        return query, params

    async def fetch_candidate_refs(
        self,
        filters: FilterState | None,
        excluded_tconsts: set[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        criteria = criteria_from_filters(filters)
        return await self.fetch_candidate_refs_for_criteria(
            criteria,
            excluded_tconsts,
            limit,
        )

    async def _sample_from_cached_pool(
        self,
        criteria: MovieCriteria,
        excluded_tconsts: set[str],
        limit: int,
    ) -> list[dict[str, Any]] | None:
        """Try to satisfy the request from the cached filter pool.

        Returns None on miss, empty list when pool exists but yields
        nothing after exclusion, or a sampled subset on hit.
        """
        return await self._filter_pool_cache.sample(
            criteria=criteria,
            excluded_tconsts=excluded_tconsts,
            limit=limit,
        )

    async def _store_filter_pool(
        self,
        criteria: MovieCriteria,
        excluded_tconsts: set[str],
        refs: list[dict[str, Any]],
    ) -> None:
        await self._filter_pool_cache.store(criteria=criteria, refs=refs)

    async def fetch_candidate_refs_for_criteria(
        self,
        criteria: MovieCriteria,
        excluded_tconsts: set[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        desired_limit = max(1, limit)

        # Fast path: serve from the cached filter pool if available.
        cached = await self._sample_from_cached_pool(criteria, excluded_tconsts, desired_limit)
        if cached:
            return cached

        # Miss path: fetch a larger pool so subsequent callers in the TTL
        # window can sample from it without re-querying. The pool size is
        # capped so the shuffle_key ORDER BY stays cheap.
        fetch_limit = max(desired_limit, _FILTER_RESULT_POOL_SIZE)

        for bucket_count in SELECTION_BUCKET_STEPS:
            buckets = random.sample(range(SAMPLE_BUCKET_COUNT), bucket_count)
            query, params = self._build_candidate_query(
                criteria=criteria,
                excluded_tconsts=excluded_tconsts,
                desired_limit=fetch_limit,
                buckets=buckets,
                use_fulltext=True,
            )
            try:
                rows = await self.db_pool.execute(query, params, fetch="all")
            except DatabaseError as exc:
                if not criteria.get("genres") or not is_fulltext_index_error(exc):
                    raise
                logger.warning(
                    "movie_candidates FULLTEXT genre search failed; retrying with LIKE fallback: %s",
                    exc,
                )
                fallback_query, fallback_params = self._build_candidate_query(
                    criteria=criteria,
                    excluded_tconsts=excluded_tconsts,
                    desired_limit=fetch_limit,
                    buckets=buckets,
                    use_fulltext=False,
                )
                rows = await self.db_pool.execute(fallback_query, fallback_params, fetch="all")
            if rows:
                pool: list[dict[str, Any]] = []
                seen: set[str] = set()
                for row in rows:
                    tconst = row["tconst"]
                    if tconst in seen:
                        continue
                    seen.add(tconst)
                    pool.append(_ref_from_row(row))
                    if len(pool) >= fetch_limit:
                        break
                await self._store_filter_pool(criteria, excluded_tconsts, pool)
                # Return only what the caller asked for, randomized so
                # two concurrent callers miss the cache → see different orders.
                random.shuffle(pool)
                return pool[:desired_limit]

        return []

    async def validate_bucket_distribution(self, table_name: str = "movie_candidates_next") -> None:
        return await self._maintainer.validate_bucket_distribution(table_name)

    async def refresh_movie_candidates(self) -> dict[str, int]:
        """Refresh the candidate table. Returns prev/new row counts for callers.

        The return shape is ``{"prev_count": int, "new_count": int}`` so a
        caller like ``refresh_movie_candidates_job`` can decide whether to
        invalidate downstream count caches. A near-zero delta is the steady
        state (title.basics + title.ratings change slowly) and does not
        warrant a count-cache stampede.
        """
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

    async def refresh_movie_candidates(self) -> dict[str, int]:
        logger.info("Refreshing movie_candidates")
        prev_count_row = await self.db_pool.execute(
            "SELECT COUNT(*) AS total FROM movie_candidates",
            fetch="one",
        )
        prev_count = int(prev_count_row["total"]) if prev_count_row else 0
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
                shuffle_key INT NOT NULL,
                refreshed_at DATETIME(6) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            fetch="none",
        )
        await self.db_pool.execute(
            f"""
            INSERT INTO movie_candidates_next (
                tconst, primaryTitle, startYear, genres, language, titleType, slug,
                averageRating, numVotes, sample_bucket, shuffle_key, refreshed_at
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
                MOD(CRC32(tb.tconst), {SAMPLE_BUCKET_COUNT}),
                CAST(RAND() * {_MYSQL_INT_MAX} AS UNSIGNED),
                UTC_TIMESTAMP(6)
            FROM `title.basics` tb
            LEFT JOIN `title.ratings` tr ON tb.tconst = tr.tconst
            WHERE tb.titleType = 'movie' AND tb.startYear IS NOT NULL
            """,
            fetch="none",
        )
        # Single ALTER TABLE building all five indexes at once. MySQL 8 builds
        # the B-tree indexes in a single table scan instead of five, and the
        # FULLTEXT index is co-scheduled rather than triggering a second full
        # scan. The _next table is not visible to readers until the RENAME
        # swap below, so a table-copy path (if the combined ALTER chooses one)
        # is safe.
        await self.db_pool.execute(
            """
            ALTER TABLE movie_candidates_next
              ADD INDEX idx_movie_candidates_bucket_filter (titleType, sample_bucket, numVotes, averageRating, startYear),
              ADD INDEX idx_movie_candidates_next_filter (titleType, startYear, averageRating, numVotes, sample_bucket),
              ADD INDEX idx_movie_candidates_next_language (language),
              ADD INDEX idx_movie_candidates_next_slug (slug(191)),
              ADD INDEX idx_movie_candidates_next_refreshed_at (refreshed_at),
              ADD FULLTEXT INDEX ftx_movie_candidates_next_genres (genres)
            """,
            fetch="none",
        )

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
        new_count = int(row["total"])
        await self.db_pool.execute("DROP TABLE IF EXISTS movie_candidates_prev", fetch="none")
        logger.info(
            "movie_candidates refresh complete prev=%d new=%d delta=%d",
            prev_count,
            new_count,
            new_count - prev_count,
        )
        return {"prev_count": prev_count, "new_count": new_count}
