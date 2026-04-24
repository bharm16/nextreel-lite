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
    (3) title contains the term. Within each bucket rows are ordered by
    ``averageRating`` desc.

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

    sql = (
        "SELECT mc.tconst, mc.primaryTitle, mc.startYear, mc.averageRating, "
        "       JSON_UNQUOTE(JSON_EXTRACT(mp.payload_json, '$.poster_url')) AS poster_url "
        "FROM movie_candidates mc "
        "LEFT JOIN movie_projection mp "
        "  ON mp.tconst = mc.tconst "
        "  AND mp.projection_state IN ('ready', 'stale') "
        "WHERE mc.primaryTitle IS NOT NULL "
        "  AND (mc.primaryTitle = %s "
        "       OR mc.primaryTitle LIKE %s ESCAPE '|' "
        "       OR mc.primaryTitle LIKE %s ESCAPE '|') "
        "ORDER BY "
        "  CASE "
        "    WHEN mc.primaryTitle = %s THEN 0 "
        "    WHEN mc.primaryTitle LIKE %s ESCAPE '|' THEN 1 "
        "    ELSE 2 "
        "  END, "
        "  COALESCE(mc.averageRating, 0) DESC "
        "LIMIT %s"
    )

    params = [escaped, prefix, contains, escaped, prefix, int(limit)]
    return sql, params
