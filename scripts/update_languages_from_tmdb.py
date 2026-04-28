#!/usr/bin/env python3
"""Repair ``title.basics.language`` using TMDb's ``original_language``.

Targets two classes of bad rows:

1. ``language IS NULL`` — never tagged.
2. Non-ISO-639-1 shapes — legacy full-word strings ("English", "Hindi"),
   multi-language concatenations ("MalayalamEnglish"), and any value that
   does not match the ``[a-z]{2,3}`` pattern.

The candidate-store query (``movies/candidate_store.py``) matches the ISO
code plus a single legacy English-name spelling. Anything else is silently
excluded from filtered results, so leaving these rows in place produces
the bug where Indian films tagged ``"MalayalamEnglish"`` leak past an
English filter or the user's chosen language pool shrinks.

Usage:
    python scripts/update_languages_from_tmdb.py            # interactive
    python scripts/update_languages_from_tmdb.py --yes      # no prompt
    python scripts/update_languages_from_tmdb.py --limit 500
    python scripts/update_languages_from_tmdb.py --only null
    python scripts/update_languages_from_tmdb.py --only dirty
"""

import argparse
import asyncio

from logging_config import get_logger
from movies.candidate_store import CandidateStore
from movies.tmdb_client import TMDbHelper

logger = get_logger(__name__)

# Title selection — repair anything voted on by a real audience. Earlier
# versions of this script restricted to ``startYear >= 2020`` for cost
# reasons; the legacy-string contamination predates that and is dominated
# by older Indian and European films, so the year gate is dropped.
_DEFAULT_MIN_VOTES = 1000
_DEFAULT_LIMIT = 5000

# Rows are bad when language is NULL or doesn't look like an ISO 639-1
# code. Two-or-three lowercase letters covers ``en``/``zh``/``cmn`` and
# excludes ``"English"``, ``"MalayalamEnglish"``, ``"None"``, etc.
_BAD_LANGUAGE_PREDICATE = (
    "(tb.language IS NULL OR tb.language NOT REGEXP '^[a-z]{2,3}$')"
)
_NULL_ONLY_PREDICATE = "tb.language IS NULL"
_DIRTY_ONLY_PREDICATE = (
    "tb.language IS NOT NULL AND tb.language NOT REGEXP '^[a-z]{2,3}$'"
)
# ``verify-en`` mode catches the ``Pushpa: The Rise`` / ``Drishyam 2``
# class — rows where ``language='en'`` is structurally valid (a real ISO
# 639-1 code) but semantically wrong (the film is Telugu/Hindi/etc.).
# These never appear under the dirty/null predicates because the value
# parses as a clean code; we have to compare against TMDb's
# ``original_language`` for every row tagged ``en`` to find them.
_VERIFY_EN_PREDICATE = "tb.language = 'en'"


def _build_select_query(predicate: str, *, with_max_votes: bool) -> str:
    max_clause = "AND tr.numVotes <= %s " if with_max_votes else ""
    return (
        "SELECT tb.tconst, tb.primaryTitle, tb.startYear, tb.language "
        "FROM `title.basics` tb "
        "JOIN `title.ratings` tr ON tb.tconst = tr.tconst "
        f"WHERE {predicate} "
        "AND tb.titleType = 'movie' "
        "AND tr.numVotes >= %s "
        f"{max_clause}"
        "ORDER BY tr.numVotes DESC "
        "LIMIT %s"
    )


async def update_languages(
    *,
    limit: int,
    min_votes: int,
    max_votes: int | None,
    predicate: str,
) -> None:
    from infra.pool import DatabaseConnectionPool
    from settings import Config

    db_pool = DatabaseConnectionPool(Config.get_db_config())
    await db_pool.init_pool()

    tmdb = TMDbHelper()

    print("Finding movies with bad language values...")

    try:
        select_query = _build_select_query(predicate, with_max_votes=max_votes is not None)
        params: list = [min_votes]
        if max_votes is not None:
            params.append(max_votes)
        params.append(limit)
        movies = await db_pool.execute(select_query, params, fetch="all") or []
        print(f"Found {len(movies)} movies to repair")

        updated = 0
        unchanged = 0
        failed = 0

        for movie in movies:
            tconst = movie["tconst"]
            old_value = movie.get("language")
            try:
                tmdb_id = await tmdb.get_tmdb_id_by_tconst(tconst)
                if not tmdb_id:
                    unchanged += 1
                    continue

                movie_info = await tmdb.get_movie_info_by_tmdb_id(tmdb_id)
                if not movie_info:
                    unchanged += 1
                    continue

                language = movie_info.get("original_language")
                if not language:
                    unchanged += 1
                    continue
                if language == old_value:
                    unchanged += 1
                    continue

                await db_pool.execute(
                    "UPDATE `title.basics` SET language = %s WHERE tconst = %s",
                    [language, tconst],
                    fetch="rowcount",
                )
                updated += 1
                logger.info(
                    "Repaired %s: language %r -> %r",
                    tconst,
                    old_value,
                    language,
                )

                if updated % 100 == 0:
                    logger.info("Updated %d movies so far...", updated)

            except Exception as e:
                logger.error("Error updating %s: %s", tconst, e)
                failed += 1

            # TMDB rate limit — 40 requests per 10 seconds.
            await asyncio.sleep(0.3)

        print()
        print("Completed!")
        print(f"  Repaired:  {updated}")
        print(f"  Unchanged: {unchanged}")
        print(f"  Failed:    {failed}")

        if updated > 0:
            logger.info(
                "Rebuilding movie_candidates after repairing %d language rows",
                updated,
            )
            candidate_store = CandidateStore(db_pool)
            await candidate_store.refresh_movie_candidates()

        dist_query = (
            "SELECT COALESCE(language, 'NULL') AS lang, COUNT(*) AS count "
            "FROM `title.basics` "
            "WHERE titleType = 'movie' "
            "GROUP BY language ORDER BY count DESC LIMIT 15"
        )
        results = await db_pool.execute(dist_query, fetch="all") or []
        print("\nTop language values after repair:")
        for r in results:
            print(f"  {r['lang']}: {r['count']} movies")

    finally:
        await tmdb.close()
        await db_pool.close_pool()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        help=f"Max rows to repair in this run (default {_DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--min-votes",
        type=int,
        default=_DEFAULT_MIN_VOTES,
        help=f"Skip rows with fewer than this many IMDb votes (default {_DEFAULT_MIN_VOTES}).",
    )
    parser.add_argument(
        "--max-votes",
        type=int,
        default=None,
        help=(
            "Skip rows with more votes than this. Useful for verify-en mode "
            "to target the mid-tier where wrong-ISO mistags concentrate."
        ),
    )
    parser.add_argument(
        "--only",
        choices=("null", "dirty", "all", "verify-en"),
        default="all",
        help=(
            "Restrict to NULL rows, non-NULL legacy/dirty rows, both, or "
            "verify-en (rows tagged 'en' but semantically wrong — fixes "
            "Pushpa/Drishyam-class mistags). Default: all."
        ),
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    predicate = {
        "null": _NULL_ONLY_PREDICATE,
        "dirty": _DIRTY_ONLY_PREDICATE,
        "all": _BAD_LANGUAGE_PREDICATE,
        "verify-en": _VERIFY_EN_PREDICATE,
    }[args.only]

    print("=" * 60)
    print("TMDB Language Repair Script")
    print("=" * 60)
    print(f"\nMode: {args.only}  |  limit: {args.limit}  |  min_votes: {args.min_votes}")
    print("Repairs NULL and non-ISO-639-1 language values in title.basics.\n")

    if not args.yes:
        response = input("Do you want to proceed? (y/n): ")
        if response.lower() != "y":
            print("Cancelled.")
            return

    asyncio.run(
        update_languages(
            limit=args.limit,
            min_votes=args.min_votes,
            max_votes=args.max_votes,
            predicate=predicate,
        )
    )


if __name__ == "__main__":
    main()
