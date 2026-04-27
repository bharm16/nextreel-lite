"""Title-search SQL used by the navbar Spotlight modal (`/api/search`).

Lives outside ``MovieQueryBuilder`` because it has nothing in common with the
random-movie criteria builder — different table, different intent, different
escaping strategy.
"""

from __future__ import annotations


def build_search_query(raw_query: str, limit: int = 10) -> tuple[str | None, list | None]:
    """Build a parameterized title-search query against ``movie_candidates``.

    ``movie_candidates`` is the denormalized cache table (see
    ``infra/runtime_schema.py``) populated by the candidate refresh job.
    It stores ``primaryTitle``, ``startYear``, and ``averageRating`` in typed
    columns — perfect for fast LIKE-based title lookup. Director and poster
    data live in ``movie_projection.payload_json`` and require per-movie
    enrichment; the search UI intentionally omits them.

    Returns ``(sql, params)`` — or ``(None, None)`` when the query is below
    the minimum length threshold. Callers should short-circuit on None
    without hitting the DB.

    Ranks results by: (1) exact title match, (2) title starts with the term,
    (3) title starts with ``The``/``A``/``An`` + term, (4) title contains
    the term. Within each bucket rows are ordered by ``numVotes`` desc
    (popularity proxy) and then ``averageRating``. Without the article-stripped
    bucket a search for ``godfather`` buried the classic ``The Godfather``
    below dozens of obscure exact-name matches.

    Metacharacters (``%``, ``_``, and the ``|`` escape char) in the user
    query are escaped so they're treated as literal characters.
    """
    cleaned = (raw_query or "").strip()
    if len(cleaned) < 2:
        return None, None

    # Escape SQL LIKE metacharacters using '|' as the escape char.
    escaped = cleaned.replace("|", "||").replace("%", "|%").replace("_", "|_")

    prefix = f"{escaped}%"
    contains = f"%{escaped}%"
    article_the = f"The {escaped}%"
    article_a = f"A {escaped}%"
    article_an = f"An {escaped}%"

    # Params order is kept compatible with the historical test contract —
    # params[0]=exact, [1]=prefix, [2]=contains — so downstream assertions
    # don't break when we add article-stripped buckets.
    sql = (
        "SELECT mc.tconst, mp.public_id, mc.primaryTitle, mc.startYear, "
        "       mc.averageRating, "
        "       CASE WHEN mp.projection_state IN ('ready', 'stale') "
        "            THEN JSON_UNQUOTE(JSON_EXTRACT(mp.payload_json, '$.poster_url')) "
        "            ELSE NULL END AS poster_url "
        # Unconditional LEFT JOIN so public_id flows through for every state
        # (core/ready/stale/failed all carry one). The CASE above keeps the
        # poster restricted to enriched states — unenriched payloads have no
        # poster_url key, but being explicit guards against future shape drift.
        "FROM movie_candidates mc "
        "LEFT JOIN movie_projection mp ON mp.tconst = mc.tconst "
        "WHERE mc.primaryTitle IS NOT NULL "
        "  AND (mc.primaryTitle = %s "
        "       OR mc.primaryTitle LIKE %s ESCAPE '|' "
        "       OR mc.primaryTitle LIKE %s ESCAPE '|' "
        "       OR mc.primaryTitle LIKE %s ESCAPE '|' "
        "       OR mc.primaryTitle LIKE %s ESCAPE '|' "
        "       OR mc.primaryTitle LIKE %s ESCAPE '|') "
        # Popularity (numVotes) dominates because the bucket approach alone
        # buried ``The Godfather`` (2.15M votes) under obscure exact matches
        # like ``Godfather`` (8K votes). Within similar popularity the bucket
        # rank still acts as a tiebreaker so a search for "it" surfaces the
        # exact "It" film ahead of coincidental contains-matches.
        "ORDER BY "
        "  COALESCE(mc.numVotes, 0) DESC, "
        "  CASE "
        "    WHEN mc.primaryTitle = %s THEN 0 "
        "    WHEN mc.primaryTitle LIKE %s ESCAPE '|' THEN 1 "
        "    WHEN mc.primaryTitle LIKE %s ESCAPE '|' "
        "         OR mc.primaryTitle LIKE %s ESCAPE '|' "
        "         OR mc.primaryTitle LIKE %s ESCAPE '|' THEN 2 "
        "    ELSE 3 "
        "  END, "
        "  COALESCE(mc.averageRating, 0) DESC "
        "LIMIT %s"
    )

    params = [
        # WHERE: exact, prefix, contains, article_the, article_a, article_an
        escaped, prefix, contains, article_the, article_a, article_an,
        # CASE: exact, prefix, article_the, article_a, article_an
        escaped, prefix, article_the, article_a, article_an,
        int(limit),
    ]
    return sql, params
