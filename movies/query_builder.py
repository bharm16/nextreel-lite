"""Genre-clause and title-search SQL helpers used by the live request path.

Historically this module also held ``ImdbRandomMovieFetcher`` and a multi-table
cache routing layer (``popular_movies_cache`` / ``recent_movies_cache``) — both
of which were superseded by ``movies/candidate_store.py`` and the
``movie_candidates`` denormalized cache table. They are deleted; the live read
path goes through ``CandidateStore.fetch_candidate_refs``.
"""

from __future__ import annotations

from typing import Any

from logging_config import get_logger

logger = get_logger(__name__)


def is_fulltext_index_error(exc: Exception) -> bool:
    """Return True when *exc* looks like a MySQL FULLTEXT index failure.

    Used by ``CandidateStore.fetch_candidate_refs_for_criteria`` to decide
    whether to retry a failing FULLTEXT query against the LIKE fallback.
    """
    return "fulltext" in str(exc).lower()


class MovieQueryBuilder:
    """Genre-clause builder shared by the candidate-fetch path."""

    @staticmethod
    def build_genre_conditions_fulltext(criteria: dict[str, Any], use_cache: bool = False) -> tuple:
        """Build genre conditions using FULLTEXT search."""
        genres = criteria.get("genres")
        if not genres:
            return "", []

        # If 15+ genres selected, it's essentially "any genre" - skip the filter entirely
        if len(genres) >= 15:
            logger.info(
                "%d genres selected (15+), skipping genre filter for performance", len(genres)
            )
            return "", []

        table_alias = "" if use_cache else "tb."

        # Strip boolean-mode operators to prevent injection via crafted genre names.
        _ft_unsafe = str.maketrans("", "", '+-<>()~*"@')
        genre_search = " ".join([f'+"{genre.translate(_ft_unsafe)}"' for genre in genres])
        condition = f" AND MATCH({table_alias}genres) AGAINST(%s IN BOOLEAN MODE)"
        return condition, [genre_search]

    @staticmethod
    def _escape_like(value: str) -> str:
        """Escape SQL LIKE wildcards (``%`` and ``_``) in *value*."""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def build_genre_conditions(
        criteria: dict[str, Any], parameters: list[Any], use_cache: bool = False
    ) -> list[str]:
        """Fallback to LIKE queries if FULLTEXT is not available."""
        genre_conditions: list[str] = []
        genres = criteria.get("genres")
        if genres:
            if len(genres) >= 15:
                logger.info(
                    "%d genres selected (15+), skipping genre filter for performance", len(genres)
                )
                return []

            if use_cache:
                genre_conditions = [" AND ".join(["genres LIKE %s" for _ in genres])]
            else:
                genre_conditions = [" AND ".join(["tb.genres LIKE %s" for _ in genres])]
            parameters.extend(
                ["%" + MovieQueryBuilder._escape_like(genre) + "%" for genre in genres]
            )
        return genre_conditions

    @staticmethod
    def genre_clause(
        criteria: dict[str, Any],
        *,
        use_fulltext: bool = True,
        use_cache: bool = False,
    ) -> tuple[str, list[Any]]:
        """Unified genre-clause builder.

        Dispatches to the FULLTEXT or LIKE variant depending on
        *use_fulltext*. Returns ``(clause, params)`` where *clause* is an
        empty string when no genre filter applies.
        """
        if use_fulltext:
            return MovieQueryBuilder.build_genre_conditions_fulltext(criteria, use_cache=use_cache)
        genre_params: list[Any] = []
        genre_conditions = MovieQueryBuilder.build_genre_conditions(
            criteria, genre_params, use_cache=use_cache
        )
        if not genre_conditions:
            return "", []
        return f" AND ({genre_conditions[0]})", genre_params

    # ── Exclude-genre clauses ─────────────────────────────────────────
    #
    # The chip filter UI emits ``exclude_genres[]`` for genres the user
    # has explicitly marked as exclusions (e.g. "no Drama, no War").
    # FULLTEXT path: ``AND NOT MATCH(genres) AGAINST('"Drama" "War"' IN
    # BOOLEAN MODE)`` — bare quoted terms (no leading ``+``) so OR
    # semantics under MATCH; the outer NOT then rejects rows that match
    # *any* excluded genre. LIKE fallback uses ``genres NOT LIKE %s``
    # joined with AND so a row is rejected if *any* excluded term appears.

    @staticmethod
    def build_exclude_genre_conditions_fulltext(
        criteria: dict[str, Any], use_cache: bool = False
    ) -> tuple[str, list[Any]]:
        excluded = criteria.get("exclude_genres")
        if not excluded:
            return "", []

        table_alias = "" if use_cache else "tb."
        _ft_unsafe = str.maketrans("", "", '+-<>()~*"@')
        sanitized = [g.translate(_ft_unsafe) for g in excluded]

        if len(sanitized) == 1:
            # Single term: keep the leading ``+`` for parity with the
            # include path so a one-term query still produces a strong
            # match phrase.
            phrase = f'+"{sanitized[0]}"'
        else:
            # Multi-term: bare quoted terms = OR semantics in BOOLEAN
            # MODE. The outer NOT rejects rows that match any of them.
            phrase = " ".join(f'"{g}"' for g in sanitized)

        condition = f" AND NOT MATCH({table_alias}genres) AGAINST(%s IN BOOLEAN MODE)"
        return condition, [phrase]

    @staticmethod
    def build_exclude_genre_conditions(
        criteria: dict[str, Any], use_cache: bool = False
    ) -> tuple[str, list[Any]]:
        excluded = criteria.get("exclude_genres")
        if not excluded:
            return "", []

        column = "genres" if use_cache else "tb.genres"
        not_like_terms = [f"{column} NOT LIKE %s" for _ in excluded]
        clause = " AND " + " AND ".join(not_like_terms)
        params = ["%" + MovieQueryBuilder._escape_like(g) + "%" for g in excluded]
        return clause, params

    @staticmethod
    def exclude_genre_clause(
        criteria: dict[str, Any],
        *,
        use_fulltext: bool = True,
        use_cache: bool = False,
    ) -> tuple[str, list[Any]]:
        """Unified exclude-genre clause builder.

        Mirrors :meth:`genre_clause` but produces a NOT MATCH / NOT LIKE
        clause that rejects rows containing any of the excluded genres.
        Returns ``(clause, params)`` where *clause* is empty when nothing
        is excluded.
        """
        if use_fulltext:
            return MovieQueryBuilder.build_exclude_genre_conditions_fulltext(
                criteria, use_cache=use_cache
            )
        return MovieQueryBuilder.build_exclude_genre_conditions(criteria, use_cache=use_cache)
