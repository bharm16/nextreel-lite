# Landing Page Discovery Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the centered hero with a side-by-side layout (backdrop left, content right) carrying a value-prop headline, subtitle, CTAs, and an interactive filter pill row that rerolls the hero in place. URL-backed sticky filter state.

**Architecture:** Path A pure (no auth-aware branching). Backend: extend `fetch_random_landing_film` with an optional `criteria` arg, add `/api/landing-film` JSON endpoint, modify `home()` to read URL params. Frontend: rewrite `home.html`, add side-by-side CSS, add a vanilla JS module for pill clicks + history API + DOM swap on JSON-fetched film.

**Tech Stack:** Quart (async Flask), Jinja2 templates, vanilla JS (no framework), Tailwind/custom CSS in `static/css/input.css`, MySQL via `infra/pool.py`, pytest-asyncio.

**Source spec:** [docs/superpowers/specs/2026-04-26-landing-page-discovery-redesign-design.md](2026-04-26-landing-page-discovery-redesign-design.md)

**Author note on commits:** The user has a "no autocommit — make edits, stop at the diff; user commits themselves" preference. Each task ends with a commit step for *documentation of intent*. When executing inline, pause for `git diff` review and let the user commit at task boundaries. When executing via subagent, the subagent may commit and the user reviews per task boundary.

---

## File Structure

### Create
| Path | Responsibility |
|---|---|
| `movies/landing_filter_url.py` | URL query-param ↔ internal criteria translation. Pure functions. Allowlists for genre, decade, runtime, rating values. |
| `tests/movies/test_landing_filter_url.py` | Unit tests for the translation helpers. |
| `static/js/landing-pills.js` | Vanilla JS module: pill click handlers, fetch `/api/landing-film`, DOM swap, History API for URL state, popstate handler, reduced-motion respect. |

### Modify
| Path | Change |
|---|---|
| `movies/landing_film_service.py` | Add optional `criteria: dict \| None = None` arg to `fetch_random_landing_film`; add `_fetch_random_filtered` path that uses `MovieQueryBuilder` to constrain `movie_projection` rows. |
| `tests/movies/test_landing_film_service.py` | Add tests for the filter-aware fetch path. |
| `nextreel/web/routes/movies.py` | Modify `home()` to read URL params and render active pills + conditional CTA form action; add `/api/landing-film` JSON endpoint. |
| `tests/web/test_routes_home.py` | Add tests for URL-aware rendering and the JSON endpoint. |
| `templates/home.html` | Full body rewrite — side-by-side hero, no kicker, no side label, pill row, conditional form action, empty-state branch. |
| `static/css/input.css` | Remove `.landing-kicker*` and `.landing-side-label` rules. Add side-by-side flex layout, pill row styles, mobile breakpoint, empty-state styles. |
| `static/css/output.css` | Regenerate via `npm run build-css`. |

---

## Task 1: URL filter schema translation helper

Pure functions that translate URL query params → internal `criteria` dict and back to template-friendly form-schema dict. TDD-driven because these are pure and easy to test.

**Files:**
- Create: `movies/landing_filter_url.py`
- Test: `tests/movies/test_landing_filter_url.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/movies/test_landing_filter_url.py`:

```python
"""Tests for movies.landing_filter_url translation helpers."""

from __future__ import annotations

from movies.landing_filter_url import (
    active_filters_for_template,
    criteria_from_query_args,
)


def test_criteria_from_empty_args_returns_empty():
    assert criteria_from_query_args({}) == {}


def test_criteria_genre_drama():
    assert criteria_from_query_args({"genre": "Drama"}) == {"genres": ["Drama"]}


def test_criteria_genre_invalid_dropped():
    assert criteria_from_query_args({"genre": "NotARealGenre"}) == {}


def test_criteria_genre_case_sensitive():
    """VALID_GENRES is case-sensitive — lowercase is dropped."""
    assert criteria_from_query_args({"genre": "drama"}) == {}


def test_criteria_decade_1990s():
    result = criteria_from_query_args({"decade": "1990s"})
    assert result == {"min_year": 1990, "max_year": 1999}


def test_criteria_decade_invalid_dropped():
    assert criteria_from_query_args({"decade": "1990"}) == {}
    assert criteria_from_query_args({"decade": "abc"}) == {}


def test_criteria_runtime_lt120():
    assert criteria_from_query_args({"runtime": "lt120"}) == {"max_runtime": 120}


def test_criteria_runtime_lt90():
    assert criteria_from_query_args({"runtime": "lt90"}) == {"max_runtime": 90}


def test_criteria_runtime_gt150():
    assert criteria_from_query_args({"runtime": "gt150"}) == {"min_runtime": 150}


def test_criteria_runtime_invalid_dropped():
    assert criteria_from_query_args({"runtime": "lt60"}) == {}


def test_criteria_rating_7plus():
    assert criteria_from_query_args({"rating": "7plus"}) == {"min_rating": 7.0}


def test_criteria_rating_6plus_and_8plus():
    assert criteria_from_query_args({"rating": "6plus"}) == {"min_rating": 6.0}
    assert criteria_from_query_args({"rating": "8plus"}) == {"min_rating": 8.0}


def test_criteria_rating_invalid_dropped():
    assert criteria_from_query_args({"rating": "5plus"}) == {}


def test_criteria_combined():
    result = criteria_from_query_args(
        {
            "genre": "Drama",
            "decade": "1990s",
            "runtime": "lt120",
            "rating": "7plus",
        }
    )
    assert result == {
        "genres": ["Drama"],
        "min_year": 1990,
        "max_year": 1999,
        "max_runtime": 120,
        "min_rating": 7.0,
    }


def test_criteria_unknown_param_ignored():
    assert criteria_from_query_args({"foo": "bar", "genre": "Drama"}) == {
        "genres": ["Drama"]
    }


def test_active_filters_for_template_empty():
    assert active_filters_for_template({}) == {}


def test_active_filters_for_template_genre_only():
    """Translates URL-state criteria back to /filtered_movie form-schema keys."""
    result = active_filters_for_template({"genres": ["Drama"]})
    assert result == {"genre": "Drama"}


def test_active_filters_for_template_decade():
    result = active_filters_for_template({"min_year": 1990, "max_year": 1999})
    assert result == {"min_year": "1990", "max_year": "1999"}


def test_active_filters_for_template_runtime():
    assert active_filters_for_template({"max_runtime": 120}) == {"max_runtime": "120"}


def test_active_filters_for_template_rating():
    assert active_filters_for_template({"min_rating": 7.0}) == {"min_rating": "7.0"}


def test_active_filters_for_template_combined():
    criteria = {
        "genres": ["Drama"],
        "min_year": 1990,
        "max_year": 1999,
        "max_runtime": 120,
        "min_rating": 7.0,
    }
    result = active_filters_for_template(criteria)
    assert result == {
        "genre": "Drama",
        "min_year": "1990",
        "max_year": "1999",
        "max_runtime": "120",
        "min_rating": "7.0",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_landing_filter_url.py -v`
Expected: All FAIL with `ModuleNotFoundError: No module named 'movies.landing_filter_url'`.

- [ ] **Step 3: Implement the module**

Create `movies/landing_filter_url.py`:

```python
"""URL query-param ↔ internal-criteria translation for the landing-page filter strip.

The landing strip exposes a narrow, hardcoded set of pills (Drama · Comedy · 1990s ·
< 120 min · 7+ rating). This module translates between the URL schema those pills
produce and two internal representations:

  - ``criteria`` — the dict shape consumed by ``fetch_random_landing_film`` and
    ultimately by ``movies.query_builder.MovieQueryBuilder``.
  - ``active_filters`` — the form-schema-keyed dict the template uses to populate
    hidden inputs in the primary CTA's POST form (when filters are active, the
    CTA submits to ``/filtered_movie``).

Invalid values are silently dropped so a malformed shared link still renders
*something*. The set of valid values is intentionally narrow — the full filter
UI on the movie detail page handles the long tail.
"""

from __future__ import annotations

from typing import Any, Mapping

from movies.filter_parser import VALID_GENRES

# ── URL-schema allowlists ─────────────────────────────────────────

_VALID_DECADES: dict[str, tuple[int, int]] = {
    "1970s": (1970, 1979),
    "1980s": (1980, 1989),
    "1990s": (1990, 1999),
    "2000s": (2000, 2009),
    "2010s": (2010, 2019),
    "2020s": (2020, 2029),
}

_VALID_RUNTIMES: dict[str, tuple[str, int]] = {
    "lt90": ("max_runtime", 90),
    "lt120": ("max_runtime", 120),
    "gt150": ("min_runtime", 150),
}

_VALID_RATINGS: dict[str, float] = {
    "6plus": 6.0,
    "7plus": 7.0,
    "8plus": 8.0,
}


def criteria_from_query_args(args: Mapping[str, str]) -> dict[str, Any]:
    """Translate a URL-arg mapping into the internal ``criteria`` dict.

    Unknown params are ignored. Invalid values for known params are silently
    dropped (the URL is treated as if the bad param weren't present).
    """
    criteria: dict[str, Any] = {}

    genre = args.get("genre")
    if isinstance(genre, str) and genre in VALID_GENRES:
        criteria["genres"] = [genre]

    decade = args.get("decade")
    if isinstance(decade, str) and decade in _VALID_DECADES:
        min_year, max_year = _VALID_DECADES[decade]
        criteria["min_year"] = min_year
        criteria["max_year"] = max_year

    runtime = args.get("runtime")
    if isinstance(runtime, str) and runtime in _VALID_RUNTIMES:
        key, value = _VALID_RUNTIMES[runtime]
        criteria[key] = value

    rating = args.get("rating")
    if isinstance(rating, str) and rating in _VALID_RATINGS:
        criteria["min_rating"] = _VALID_RATINGS[rating]

    return criteria


def active_filters_for_template(criteria: Mapping[str, Any]) -> dict[str, str]:
    """Translate ``criteria`` back to the form-schema keys ``/filtered_movie`` expects.

    Used in the home template to populate hidden inputs in the primary CTA form
    when filters are active. All values are stringified for direct ``<input value="...">``
    use.
    """
    active: dict[str, str] = {}

    genres = criteria.get("genres")
    if isinstance(genres, list) and genres:
        active["genre"] = str(genres[0])

    if "min_year" in criteria:
        active["min_year"] = str(criteria["min_year"])
    if "max_year" in criteria:
        active["max_year"] = str(criteria["max_year"])

    if "max_runtime" in criteria:
        active["max_runtime"] = str(criteria["max_runtime"])
    if "min_runtime" in criteria:
        active["min_runtime"] = str(criteria["min_runtime"])

    if "min_rating" in criteria:
        active["min_rating"] = str(criteria["min_rating"])

    return active


__all__ = ["criteria_from_query_args", "active_filters_for_template"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_landing_filter_url.py -v`
Expected: All 19 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add movies/landing_filter_url.py tests/movies/test_landing_filter_url.py
git commit -m "feat(landing): add URL ↔ criteria translation helpers

Pure functions that translate the landing-page filter pill URL schema
(genre / decade / runtime / rating) into the internal criteria dict
consumed by the landing-film picker, and back to the form-schema-keyed
dict the template uses to populate hidden inputs in the primary CTA
form when filters are active.

Invalid values silently drop so malformed shared links still render."
```

---

## Task 2: Extend `fetch_random_landing_film` with filter support

Add an optional `criteria` arg to the existing function. When `None`, use the existing fast offset-based random pick. When a dict, route through a filter-aware SQL path that constrains rows by genre / year range / runtime / rating before the random pick.

**Files:**
- Modify: `movies/landing_film_service.py`
- Test: `tests/movies/test_landing_film_service.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/movies/test_landing_film_service.py` (after the existing tests):

```python
@pytest.mark.asyncio
async def test_fetch_with_empty_criteria_uses_unfiltered_path():
    """Empty criteria dict should behave identically to None — unfiltered path."""
    pool = _make_pool(count=0, rows=None)
    result = await fetch_random_landing_film(pool, criteria={})
    # Same return as the no-arg path: None when count is zero.
    assert result is None


@pytest.mark.asyncio
async def test_fetch_filtered_genre_returns_film():
    """With genre criteria, the filtered SQL path runs and returns a film."""
    pool = AsyncMock()

    call_log: list[str] = []

    async def _execute(sql, *args, **kwargs):
        call_log.append(sql)
        if "COUNT(*)" in sql:
            return {"n": 5}
        if "ORDER BY tconst" in sql:
            # Filtered tconst lookup
            return [{"tconst": "tt0109424"}]
        # Payload lookup
        return [
            {
                "tconst": "tt0109424",
                "public_id": "abc-123",
                "payload_json": json.dumps(
                    {
                        "title": "Chungking Express",
                        "year": "1994",
                        "directors": "Wong Kar-wai",
                        "runtime": "102 min",
                        "backdrop_url": "https://image.tmdb.org/t/p/original/foo.jpg",
                    }
                ),
            }
        ]

    pool.execute = AsyncMock(side_effect=_execute)
    result = await fetch_random_landing_film(pool, criteria={"genres": ["Drama"]})

    assert result is not None
    assert result["tconst"] == "tt0109424"
    assert result["title"] == "Chungking Express"
    # Filtered path must include movie_candidates join for genre.
    assert any("movie_candidates" in sql.lower() for sql in call_log)


@pytest.mark.asyncio
async def test_fetch_filtered_year_range_returns_film():
    """With min_year/max_year criteria, the year predicate appears in SQL."""
    pool = AsyncMock()
    call_log: list[str] = []

    async def _execute(sql, *args, **kwargs):
        call_log.append(sql)
        if "COUNT(*)" in sql:
            return {"n": 3}
        if "ORDER BY tconst" in sql:
            return [{"tconst": "tt0118694"}]
        return [
            {
                "tconst": "tt0118694",
                "public_id": "def-456",
                "payload_json": {
                    "title": "In the Mood for Love",
                    "year": "2000",
                    "directors": "Wong Kar-wai",
                    "runtime": "98 min",
                    "backdrop_url": "https://image.tmdb.org/t/p/original/bar.jpg",
                },
            }
        ]

    pool.execute = AsyncMock(side_effect=_execute)
    result = await fetch_random_landing_film(
        pool, criteria={"min_year": 1990, "max_year": 1999}
    )

    assert result is not None
    assert result["title"] == "In the Mood for Love"
    # Year predicate should be present in the filtered tconst lookup.
    filtered_sqls = [sql for sql in call_log if "ORDER BY tconst" in sql]
    assert filtered_sqls
    assert any("year" in sql.lower() for sql in filtered_sqls)


@pytest.mark.asyncio
async def test_fetch_filtered_no_matching_rows_returns_none():
    """When the filter yields zero rows, return None — caller handles empty state."""
    pool = AsyncMock()

    async def _execute(sql, *args, **kwargs):
        if "COUNT(*)" in sql:
            return {"n": 0}
        return []

    pool.execute = AsyncMock(side_effect=_execute)
    result = await fetch_random_landing_film(
        pool, criteria={"genres": ["Drama"], "min_year": 1990, "max_year": 1999}
    )
    assert result is None


@pytest.mark.asyncio
async def test_fetch_filtered_skips_films_without_real_backdrop():
    """A film with the placeholder backdrop URL is skipped in the filtered path."""
    pool = AsyncMock()

    async def _execute(sql, *args, **kwargs):
        if "COUNT(*)" in sql:
            return {"n": 5}
        if "ORDER BY tconst" in sql:
            return [{"tconst": "tt0001"}, {"tconst": "tt0002"}]
        return [
            {
                "tconst": "tt0001",
                "public_id": None,
                "payload_json": {
                    "title": "Bad",
                    "year": "1999",
                    "directors": "X",
                    "runtime": "90 min",
                    "backdrop_url": "/static/img/backdrop-placeholder.svg",
                },
            },
            {
                "tconst": "tt0002",
                "public_id": "ok-id",
                "payload_json": {
                    "title": "Good",
                    "year": "1999",
                    "directors": "Y",
                    "runtime": "100 min",
                    "backdrop_url": "https://image.tmdb.org/t/p/original/x.jpg",
                },
            },
        ]

    pool.execute = AsyncMock(side_effect=_execute)
    result = await fetch_random_landing_film(pool, criteria={"min_year": 1990, "max_year": 1999})
    assert result is not None
    assert result["tconst"] == "tt0002"


@pytest.mark.asyncio
async def test_fetch_filtered_swallows_db_errors_and_returns_none():
    """Filtered path must degrade silently, like the unfiltered path."""
    pool = AsyncMock()

    async def _execute(sql, *args, **kwargs):
        raise RuntimeError("DB on fire")

    pool.execute = AsyncMock(side_effect=_execute)
    result = await fetch_random_landing_film(pool, criteria={"genres": ["Drama"]})
    assert result is None
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_landing_film_service.py -v -k "filtered or empty_criteria"`
Expected: FAIL — the new tests reference behavior the function doesn't yet support.

- [ ] **Step 3: Implement the filtered fetch path**

Modify `movies/landing_film_service.py`:

Replace the existing `fetch_random_landing_film` function and add the new helpers below. The full file should look like this (existing imports and constants preserved at top):

```python
async def fetch_random_landing_film(
    pool, criteria: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Pick one enriched film with a TMDb-sourced backdrop, at random.

    When ``criteria`` is None or empty, uses the fast unfiltered offset path
    against all READY rows. When ``criteria`` is provided, routes through a
    filter-aware path that constrains rows by genre / year range / runtime /
    rating before the random pick.

    Returns a flat dict ready for template use, or None if no qualifying
    rows exist. Callers should apply a hardcoded fallback only on the
    unfiltered path; on the filtered path, None signals empty-state UX.
    """
    if not criteria:
        return await _fetch_random_unfiltered(pool)
    return await _fetch_random_filtered(pool, criteria)


async def _fetch_random_unfiltered(pool) -> dict[str, Any] | None:
    """Existing fast offset-based random pick across all READY rows."""
    try:
        total = await _ready_row_count(pool)
    except Exception as exc:  # noqa: BLE001 — defend the landing page
        logger.warning("Landing-film count query failed: %s", exc)
        return None

    if total <= 0:
        return None

    offset = random.randint(0, max(0, total - _LANDING_CANDIDATE_POOL_SIZE))
    try:
        id_rows = await pool.execute(
            """
            SELECT tconst
            FROM movie_projection
            WHERE projection_state = 'ready'
            ORDER BY tconst
            LIMIT %s OFFSET %s
            """,
            (_LANDING_CANDIDATE_POOL_SIZE, offset),
            fetch="all",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Landing-film tconst query failed: %s", exc)
        return None

    return await _hydrate_first_with_real_backdrop(pool, id_rows)


async def _fetch_random_filtered(
    pool, criteria: dict[str, Any]
) -> dict[str, Any] | None:
    """Filter-aware random pick.

    Constrains ``movie_projection`` rows by joining ``movie_candidates`` for
    genre/runtime/rating predicates and applying year-range against the
    payload's stringified year. Then takes a random offset within the
    matching set, hydrates payloads, and returns the first one with a real
    backdrop.
    """
    where_clauses = ["mp.projection_state = 'ready'"]
    params: list[Any] = []

    genres = criteria.get("genres")
    if isinstance(genres, list) and genres:
        where_clauses.append("FIND_IN_SET(%s, mc.genres) > 0")
        params.append(genres[0])

    if "min_year" in criteria:
        where_clauses.append(
            "CAST(JSON_UNQUOTE(JSON_EXTRACT(mp.payload_json, '$.year')) AS UNSIGNED) >= %s"
        )
        params.append(int(criteria["min_year"]))
    if "max_year" in criteria:
        where_clauses.append(
            "CAST(JSON_UNQUOTE(JSON_EXTRACT(mp.payload_json, '$.year')) AS UNSIGNED) <= %s"
        )
        params.append(int(criteria["max_year"]))

    if "max_runtime" in criteria:
        where_clauses.append("mc.runtimeMinutes <= %s")
        params.append(int(criteria["max_runtime"]))
    if "min_runtime" in criteria:
        where_clauses.append("mc.runtimeMinutes >= %s")
        params.append(int(criteria["min_runtime"]))

    if "min_rating" in criteria:
        where_clauses.append("mc.averageRating >= %s")
        params.append(float(criteria["min_rating"]))

    where_sql = " AND ".join(where_clauses)

    count_sql = (
        "SELECT COUNT(*) AS n "
        "FROM movie_projection mp "
        "JOIN movie_candidates mc ON mc.tconst = mp.tconst "
        f"WHERE {where_sql}"
    )

    try:
        count_row = await pool.execute(count_sql, params, fetch="one")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Landing-film filtered count failed: %s", exc)
        return None

    total = int(count_row["n"]) if count_row and count_row.get("n") is not None else 0
    if total <= 0:
        return None

    offset = random.randint(0, max(0, total - _LANDING_CANDIDATE_POOL_SIZE))
    id_sql = (
        "SELECT mp.tconst "
        "FROM movie_projection mp "
        "JOIN movie_candidates mc ON mc.tconst = mp.tconst "
        f"WHERE {where_sql} "
        "ORDER BY mp.tconst "
        "LIMIT %s OFFSET %s"
    )
    id_params = (*params, _LANDING_CANDIDATE_POOL_SIZE, offset)

    try:
        id_rows = await pool.execute(id_sql, id_params, fetch="all")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Landing-film filtered tconst query failed: %s", exc)
        return None

    return await _hydrate_first_with_real_backdrop(pool, id_rows)


async def _hydrate_first_with_real_backdrop(
    pool, id_rows
) -> dict[str, Any] | None:
    """Common payload hydration step shared by filtered and unfiltered paths."""
    if not id_rows:
        return None

    tconsts = [row["tconst"] for row in id_rows if row.get("tconst")]
    if not tconsts:
        return None

    placeholders = ",".join(["%s"] * len(tconsts))
    try:
        rows = await pool.execute(
            f"""
            SELECT tconst, payload_json, public_id
            FROM movie_projection
            WHERE tconst IN ({placeholders})
            """,
            tconsts,
            fetch="all",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Landing-film payload query failed: %s", exc)
        return None

    if not rows:
        return None

    rows = list(rows)
    random.shuffle(rows)
    for row in rows:
        payload_raw = row["payload_json"]
        try:
            payload = (
                json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            )
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        backdrop_url = payload.get("backdrop_url")
        if not isinstance(backdrop_url, str) or not backdrop_url.startswith(
            _LANDING_TMDB_PREFIX
        ):
            continue
        return {
            "tconst": row["tconst"],
            "public_id": row.get("public_id"),
            "title": payload.get("title"),
            "year": _clean(payload.get("year")),
            "director": _clean(payload.get("directors")),
            "runtime": _clean(payload.get("runtime")),
            "backdrop_url": backdrop_url,
        }

    return None
```

The previous body of `fetch_random_landing_film` (offset pick, two-step lookup, payload hydration) has been split: `_fetch_random_unfiltered` keeps the original behavior, `_hydrate_first_with_real_backdrop` factors out the payload-walk step shared by both paths, and `_fetch_random_filtered` is the new filter-aware variant.

- [ ] **Step 4: Run all landing-film tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_landing_film_service.py -v`
Expected: All tests PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add movies/landing_film_service.py tests/movies/test_landing_film_service.py
git commit -m "feat(landing): add filter-aware fetch path

fetch_random_landing_film now accepts an optional criteria dict. When
provided, routes through a filter-aware SQL path that joins
movie_candidates for genre/runtime/rating predicates and applies a
year-range against the payload's stringified year, then takes a random
offset within the matching set.

The unfiltered path is unchanged and continues to use the in-process
count cache. The filtered path runs an uncached count per query — these
are user-paced clicks, not bursty, and the count over a filtered subset
is small enough to tolerate."
```

---

## Task 3: Add `/api/landing-film` JSON endpoint

New endpoint that takes URL filter params and returns a JSON film payload (or 204 when no match).

**Files:**
- Modify: `nextreel/web/routes/movies.py`
- Test: `tests/web/test_routes_home.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_routes_home.py`:

```python
@pytest.mark.asyncio
async def test_api_landing_film_returns_json(quart_app):
    """Unfiltered call returns a JSON film payload."""
    client = quart_app.test_client()
    resp = await client.get("/api/landing-film")
    assert resp.status_code == 200
    payload = await resp.get_json()
    assert "tconst" in payload
    assert "backdrop_url" in payload
    assert payload["backdrop_url"].startswith("https://image.tmdb.org/")


@pytest.mark.asyncio
async def test_api_landing_film_with_genre_filter(quart_app):
    """Genre filter routes through the filtered fetch path."""
    client = quart_app.test_client()
    resp = await client.get("/api/landing-film?genre=Drama")
    assert resp.status_code == 200
    payload = await resp.get_json()
    assert "tconst" in payload


@pytest.mark.asyncio
async def test_api_landing_film_returns_204_when_no_match(quart_app, monkeypatch):
    """When the filter combo has no matches, return 204 with empty body."""
    from movies import landing_film_service

    async def _no_film(*args, **kwargs):
        return None

    monkeypatch.setattr(landing_film_service, "fetch_random_landing_film", _no_film)

    client = quart_app.test_client()
    resp = await client.get("/api/landing-film?genre=Drama&decade=1970s")
    assert resp.status_code == 204
    body = await resp.get_data()
    assert body == b""


@pytest.mark.asyncio
async def test_api_landing_film_drops_invalid_params(quart_app):
    """Invalid filter values are silently dropped (returns whatever the
    unfiltered query returns)."""
    client = quart_app.test_client()
    resp = await client.get("/api/landing-film?genre=NotAGenre")
    # No criteria after dropping, so unfiltered path runs and returns 200.
    assert resp.status_code == 200
```

(Use the existing `quart_app` test fixture from `tests/conftest.py` — verify it exists; if not, the implementer will copy the bootstrap pattern from `tests/web/test_routes_home.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_routes_home.py -v -k "api_landing_film"`
Expected: FAIL — endpoint doesn't exist yet (404).

- [ ] **Step 3: Add the endpoint**

In `nextreel/web/routes/movies.py`, add the import at the top (near the other `from movies` imports):

```python
from movies.landing_filter_url import (
    active_filters_for_template,
    criteria_from_query_args,
)
```

Add the new endpoint (place near the existing `home()` function):

```python
@bp.route("/api/landing-film")
async def landing_film_json():
    """JSON endpoint for the landing-page filter pills.

    Reads URL filter params (genre, decade, runtime, rating), translates them
    to internal criteria, and returns one matching film payload as JSON.
    Returns 204 with empty body when no film matches the filter combination.
    """
    services = _services()
    criteria = criteria_from_query_args(request.args)
    film = await fetch_random_landing_film(
        services.movie_manager.db_pool, criteria
    )
    if film is None:
        return ("", 204)

    if not film.get("public_id"):
        film["public_id"] = await public_id_for_tconst(
            services.movie_manager.db_pool, film.get("tconst")
        )

    return jsonify(film)
```

If `jsonify` and `request` aren't already imported in this file, add:

```python
from quart import jsonify, request
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/web/test_routes_home.py -v -k "api_landing_film"`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/movies.py tests/web/test_routes_home.py
git commit -m "feat(landing): add /api/landing-film JSON endpoint

Reads URL filter params, translates them via criteria_from_query_args,
fetches a matching film, returns JSON payload (200) or empty body (204)
when no film matches.

Used by the landing-page filter pills to reroll the hero in place
without a full nav."
```

---

## Task 4: Modify `home()` route to read URL params

The `home()` handler now reads URL filter params on initial load, computes which pills are active, and passes both the (filtered) film and the active-filter map to the template. When criteria are present and no film matches, skip the fallback pool — render the empty state instead.

**Files:**
- Modify: `nextreel/web/routes/movies.py`
- Test: `tests/web/test_routes_home.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_routes_home.py`:

```python
@pytest.mark.asyncio
async def test_home_with_no_url_params_unfiltered(quart_app):
    """Bare / behaves as today — random film, no active filters."""
    client = quart_app.test_client()
    resp = await client.get("/")
    assert resp.status_code == 200
    body = (await resp.get_data()).decode()
    # No active-filter hidden inputs in the primary CTA form.
    assert 'name="genre"' not in body or 'value=""' in body


@pytest.mark.asyncio
async def test_home_with_genre_param_marks_pill_active(quart_app):
    """/?genre=Drama renders the Drama pill in the active state."""
    client = quart_app.test_client()
    resp = await client.get("/?genre=Drama")
    assert resp.status_code == 200
    body = (await resp.get_data()).decode()
    # Active pill has aria-pressed="true" with the matching filter value.
    assert 'data-filter-key="genre"' in body
    assert 'data-filter-value="Drama"' in body
    # The Drama pill specifically should be aria-pressed=true.
    # Crude check: the substring 'aria-pressed="true"' appears between
    # 'data-filter-value="Drama"' and the closing tag.
    drama_idx = body.find('data-filter-value="Drama"')
    assert drama_idx >= 0
    # Look 200 chars around for aria-pressed="true"
    window = body[max(0, drama_idx - 200) : drama_idx + 200]
    assert 'aria-pressed="true"' in window


@pytest.mark.asyncio
async def test_home_with_filters_active_form_posts_to_filtered_movie(quart_app):
    """When filters are active, the primary CTA form posts to /filtered_movie
    with hidden inputs mirroring the active filters.

    Form keys match what infra.filter_normalizer.normalize_filters reads:
    'genres[]' (via getlist), 'year_min', 'year_max', 'imdb_score_min'.
    Runtime criteria are deliberately dropped — normalize_filters has no
    runtime handling.
    """
    client = quart_app.test_client()
    resp = await client.get("/?genre=Drama&decade=1990s")
    assert resp.status_code == 200
    body = (await resp.get_data()).decode()
    assert 'action="/filtered_movie"' in body
    assert '<input type="hidden" name="genres[]" value="Drama"' in body
    assert '<input type="hidden" name="year_min" value="1990"' in body
    assert '<input type="hidden" name="year_max" value="1999"' in body


@pytest.mark.asyncio
async def test_home_with_filters_no_match_renders_empty_state(quart_app, monkeypatch):
    """When criteria are present and no film matches, render the empty state
    (skip the fallback pool — the user explicitly filtered)."""
    from movies import landing_film_service

    async def _no_film(*args, **kwargs):
        return None

    monkeypatch.setattr(landing_film_service, "fetch_random_landing_film", _no_film)

    client = quart_app.test_client()
    resp = await client.get("/?genre=Drama&decade=1970s")
    assert resp.status_code == 200
    body = (await resp.get_data()).decode()
    assert "No films match these filters" in body
    # The Clear filters CTA is a link, not a form.
    assert '<a class="landing-cta-primary" href="/">Clear filters</a>' in body


@pytest.mark.asyncio
async def test_home_with_no_filters_no_match_uses_fallback_pool(quart_app, monkeypatch):
    """When no criteria and no film, fallback pool is used (existing behavior)."""
    from movies import landing_film_service

    async def _no_film(*args, **kwargs):
        return None

    monkeypatch.setattr(landing_film_service, "fetch_random_landing_film", _no_film)

    client = quart_app.test_client()
    resp = await client.get("/")
    assert resp.status_code == 200
    body = (await resp.get_data()).decode()
    # Should NOT render empty state.
    assert "No films match these filters" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/web/test_routes_home.py -v -k "test_home_with"`
Expected: FAIL — `home()` doesn't yet read URL params or pass `active_filters` to the template.

- [ ] **Step 3: Modify `home()` in `nextreel/web/routes/movies.py`**

Replace the existing `home()` function with:

```python
@bp.route("/")
async def home():
    state = _current_state()
    services = _services()
    data = await services.movie_manager.home(state, legacy_session=_legacy_session())

    criteria = criteria_from_query_args(request.args)
    landing_film = await fetch_random_landing_film(
        services.movie_manager.db_pool, criteria
    )

    if landing_film is None:
        # Only fall back when the user did NOT filter — explicit filters with
        # no matches mean we render the empty state, not a hardcoded film.
        if not criteria:
            landing_film = random.choice(_LANDING_FALLBACK_POOL)

    if isinstance(landing_film, dict) and not landing_film.get("public_id"):
        landing_film = dict(landing_film)
        landing_film["public_id"] = await public_id_for_tconst(
            services.movie_manager.db_pool, landing_film.get("tconst")
        )

    active_filters = active_filters_for_template(criteria)
    # Raw URL-arg dict (only the four keys the landing strip understands) used by
    # the template for pill aria-pressed state. Distinct from active_filters,
    # which is form-schema-keyed for the /filtered_movie POST.
    url_filters = {
        k: request.args.get(k)
        for k in ("genre", "decade", "runtime", "rating")
        if request.args.get(k)
    }

    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
        landing_film=landing_film,
        active_filters=active_filters,
        url_filters=url_filters,
    )
```

The function imports (`criteria_from_query_args`, `active_filters_for_template`) were already added in Task 3.

NOTE: The template tests in Step 1 will only pass once Task 6 (template rewrite) lands — they assert against markup not yet rendered. To unblock TDD discipline:

- The first three tests (`test_home_with_no_url_params_unfiltered`, `test_home_with_genre_param_marks_pill_active`, `test_home_with_filters_active_form_posts_to_filtered_movie`, `test_home_with_filters_no_match_renders_empty_state`) — will pass after Task 6.
- The last test (`test_home_with_no_filters_no_match_uses_fallback_pool`) tests route-level behavior (no empty-state rendering when criteria empty) — should pass after Step 3 here.

Skip the template-dependent tests for this task with `pytest.mark.skip(reason="Requires template rewrite — Task 6")` if you want a clean green run for this commit, then unskip them in Task 6. **Recommended:** mark them skipped now, run only the fallback-pool test green, commit, and proceed.

- [ ] **Step 4: Mark template-dependent tests as skip and run the route-level test**

In `tests/web/test_routes_home.py`, decorate the four template-dependent tests:

```python
@pytest.mark.skip(reason="Requires template rewrite — unskip in Task 6")
@pytest.mark.asyncio
async def test_home_with_no_url_params_unfiltered(quart_app):
    ...
```

(Apply the same skip to the three other template-dependent tests.)

Run: `python3 -m pytest tests/web/test_routes_home.py -v -k "test_home_with"`
Expected: 1 PASS (`test_home_with_no_filters_no_match_uses_fallback_pool`), 4 SKIPPED.

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/movies.py tests/web/test_routes_home.py
git commit -m "feat(landing): home() reads URL filter params

The home route now translates URL query args into landing-film criteria
and passes them to fetch_random_landing_film. When criteria are present
and the query returns no rows, the fallback pool is skipped — the user
explicitly filtered, so showing a hardcoded fallback film would lie.
The empty-state UX is rendered instead (template work in a later task).

The active_filters dict (form-schema-keyed) is also passed to the
template so the primary CTA can post to /filtered_movie with hidden
inputs when filters are active."
```

---

## Task 5: CSS — side-by-side layout, pill row, mobile breakpoint, empty state

Modify `static/css/input.css`. Remove `.landing-kicker*` and `.landing-side-label` rules. Add new layout. Regenerate `output.css`.

**Files:**
- Modify: `static/css/input.css`
- Modify: `static/css/output.css` (regenerated)

- [ ] **Step 1: Locate and remove the kicker and side-label CSS in `static/css/input.css`**

Open `static/css/input.css`. Find the lines around 2723–2778 (per the existing file) containing `.landing-side-label`, `.landing-kicker`, `.landing-kicker-dot`, and the `.landing-content .landing-kicker { animation-delay: 150ms; }` rule.

Delete:
- The `.landing-side-label` block (around line 2723)
- The `.landing-kicker` block (around line 2760)
- The `.landing-kicker-dot` block (around line 2773)
- The `.landing-content .landing-kicker { animation-delay: 150ms; }` line (around line 2751)
- Any `@media (max-width: 768px)` rule that hides the side label

Also delete or disable the `.landing-content` flex-centering style — the new layout uses a side-by-side flex on the parent.

- [ ] **Step 2: Add the new side-by-side layout CSS**

After the `.landing-bg` and `.landing-gradient` rules (which are preserved unchanged), add:

```css
  /* === Side-by-side hero (2026-04-26 redesign) === */
  .landing-page {
    position: relative;
    height: 100vh;
    overflow: hidden;
    display: flex;
  }
  .landing-bg-half {
    flex: 1;
    min-width: 0;
    position: relative;
    overflow: hidden;
  }
  .landing-bg-half .landing-bg {
    position: absolute;
    inset: 0;
    z-index: 0;
  }
  .landing-bg-half .landing-gradient,
  .landing-bg-half .home-grain {
    z-index: 1;
  }
  .landing-content-half {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 60px 56px 50px;
    background: var(--color-bg-deep, #0a0807);
    color: #fff;
    position: relative;
    z-index: 4;
  }

  /* New headline (replaces the film-title-as-H1 rule) */
  .landing-headline {
    font-family: var(--font-display, 'Bebas Neue', 'Arial Narrow', sans-serif);
    font-size: clamp(40px, 5vw, 72px);
    font-weight: 400;
    line-height: 0.92;
    letter-spacing: 0.01em;
    text-transform: uppercase;
    color: #fff;
    text-shadow: 0 2px 30px rgba(0, 0, 0, 0.5);
    margin: 0 0 18px;
  }

  .landing-sub {
    font-family: 'DM Sans', system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.55;
    color: rgba(255, 255, 255, 0.82);
    max-width: 360px;
    margin: 0 0 26px;
  }

  /* Filter pills */
  .landing-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    padding-top: 20px;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    align-items: center;
  }
  .landing-pill {
    border: 1px solid rgba(255, 255, 255, 0.32);
    border-radius: 999px;
    padding: 6px 13px;
    font-family: 'DM Sans', system-ui, sans-serif;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.92);
    background: transparent;
    cursor: pointer;
    transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
  }
  .landing-pill:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(255, 255, 255, 0.6);
  }
  .landing-pill[aria-pressed="true"] {
    background: #fff;
    color: #0a0807;
    border-color: #fff;
  }
  .landing-pill[aria-pressed="true"]::after {
    content: " ×";
    margin-left: 4px;
    font-weight: 700;
  }
  .landing-pill:focus-visible {
    outline: 2px solid var(--color-accent, #d4a14b);
    outline-offset: 2px;
  }
  .landing-pill-link {
    border: none;
    background: none;
    color: rgba(255, 255, 255, 0.55);
    text-decoration: underline;
    text-underline-offset: 3px;
    padding: 6px 4px;
    font-family: inherit;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
  }

  /* Credit corner — moved to bottom-left for the side-by-side layout */
  .landing-credit {
    position: absolute;
    bottom: 20px;
    left: 28px;
    font-family: 'Merriweather', Georgia, serif;
    font-style: italic;
    font-size: 9px;
    color: rgba(255, 255, 255, 0.45);
    z-index: 4;
  }

  /* Backdrop fade transition (for in-place reroll) */
  .landing-bg.is-loading {
    opacity: 0.35;
    transition: opacity 180ms ease;
  }
  .landing-bg {
    transition: opacity 220ms ease;
  }
  @media (prefers-reduced-motion: reduce) {
    .landing-bg.is-loading,
    .landing-bg {
      transition: none;
    }
  }

  /* === Mobile (< 768px) === */
  @media (max-width: 767.98px) {
    body:has(.landing-page) {
      overflow-y: auto !important;
    }
    .landing-page {
      flex-direction: column;
      height: auto;
      min-height: 100vh;
    }
    .landing-bg-half {
      flex: none;
      aspect-ratio: 1 / 1;
      width: 100%;
    }
    .landing-content-half {
      flex: none;
      padding: 18px 20px 22px;
    }
    .landing-headline {
      font-size: 28px;
    }
    .landing-sub {
      font-size: 11px;
      margin: 0 0 14px;
    }
    .landing-actions {
      flex-direction: column;
      gap: 8px;
    }
    .landing-cta-primary,
    .landing-cta-ghost {
      width: 100%;
      max-width: 320px;
      text-align: center;
    }
    .landing-pills {
      gap: 5px;
      padding-top: 12px;
    }
    .landing-pill,
    .landing-pill-link {
      padding: 4px 10px;
      font-size: 8.5px;
    }
    .landing-credit {
      bottom: 8px;
      left: 12px;
      font-size: 9px;
    }
  }
```

- [ ] **Step 3: Regenerate `output.css`**

Run: `npm run build-css`
Expected: `static/css/output.css` is regenerated. No errors.

- [ ] **Step 4: Sanity-check the regenerated CSS**

Run: `grep -c "landing-headline\|landing-pill\|landing-bg-half\|landing-content-half" static/css/output.css`
Expected: A non-zero number (the new classes are present).

Run: `grep -c "landing-kicker\|landing-side-label" static/css/output.css`
Expected: `0` — the old classes are gone.

- [ ] **Step 5: Commit**

```bash
git add static/css/input.css static/css/output.css
git commit -m "style(landing): side-by-side hero CSS, pill row, mobile breakpoint

Removes .landing-kicker, .landing-kicker-dot, and .landing-side-label
rules. Adds .landing-bg-half / .landing-content-half flex layout for
the side-by-side hero, .landing-headline (value-prop H1), .landing-sub
(subtitle), and .landing-pill / .landing-pill-link styles for the
filter pill row.

Mobile breakpoint at 767.98px stacks the layout: backdrop becomes
square (aspect-ratio: 1/1), content tightens (smaller headline,
subtitle, pills), CTAs stack vertically, body becomes scrollable.
Backdrop fade transition supports the in-place reroll JS in a later
task; respects prefers-reduced-motion."
```

---

## Task 6: Rewrite `templates/home.html`

Replace the current centered hero markup with the side-by-side hero, including the pill row, conditional CTA form action, empty-state branch, and unskipping the template-dependent tests from Task 4.

**Files:**
- Modify: `templates/home.html`
- Modify: `tests/web/test_routes_home.py` (unskip)

- [ ] **Step 1: Rewrite `templates/home.html`**

Replace the entire body of the file with:

```jinja
{% from "macros.html" import pick_movie_button with context %}
<!DOCTYPE html>
<html lang="en" {% if server_theme %}data-theme-server="{{ server_theme }}"{% endif %}>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nextreel – Cinema Discovery</title>
  <meta name="description" content="A film you haven't seen, every time you ask. Mark what you've seen, filter the rest.">
  <link rel="preconnect" href="https://image.tmdb.org" crossorigin>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Merriweather:ital,wght@0,300;0,400;0,700;1,400&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/output.css') }}?v={{ config.get('CSS_VERSION', '1') }}">
  {% if landing_film and landing_film.backdrop_url %}
  <link rel="preload" as="image" href="{{ landing_film.backdrop_url }}" fetchpriority="high">
  {% endif %}
  <script>
    (() => {
      try {
        const pref = localStorage.getItem('nr-theme');
        if (pref === 'light' || pref === 'dark') {
          document.documentElement.setAttribute('data-theme', pref);
          return;
        }
        const server = document.documentElement.getAttribute('data-theme-server');
        if (server === 'light' || server === 'dark') {
          document.documentElement.setAttribute('data-theme', server);
        }
      } catch (e) {}
    })();
  </script>
  <style>
    body { font-family: 'DM Sans', system-ui, sans-serif; margin: 0; padding: 0; }
    @media (min-width: 768px) {
      body:has(.landing-page) { overflow: hidden; }
    }
    .home-grain {
      position: absolute; inset: 0; z-index: 1;
      pointer-events: none; opacity: 0.04;
      background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
      background-size: 256px 256px;
    }
  </style>
</head>
<body>

  <a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm">Skip to content</a>

  {% include 'navbar_modern.html' %}

  <main id="main" class="landing-page">
    <div class="landing-bg-half">
      {% if landing_film %}
        <div class="landing-bg" id="landing-bg" style="background-image: url('{{ landing_film.backdrop_url }}');"></div>
      {% else %}
        <div class="landing-bg" id="landing-bg" style="background-image: url('{{ url_for('static', filename='img/backdrop-placeholder.svg') }}');"></div>
      {% endif %}
      <div class="landing-gradient"></div>
      <div class="home-grain"></div>
      {% if landing_film and landing_film.title and landing_film.year %}
      <div class="landing-credit" id="landing-credit">Film still: {{ landing_film.title }} ({{ landing_film.year }})</div>
      {% elif landing_film and landing_film.title %}
      <div class="landing-credit" id="landing-credit">Film still: {{ landing_film.title }}</div>
      {% endif %}
    </div>

    <div class="landing-content-half">
      {% if landing_film %}
        <h1 class="landing-headline" id="landing-headline" aria-live="polite">A film you haven't seen.<br>Every time you ask.</h1>
        <p class="landing-sub" id="landing-sub">Mark what you've seen. Filter the rest. Every pick is fresh.</p>
        <div class="landing-actions">
          {% if active_filters %}
          <form method="POST" action="/filtered_movie" style="display:inline;">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            {% for k, v in active_filters.items() %}
            <input type="hidden" name="{{ k }}" value="{{ v }}">
            {% endfor %}
            <button type="submit" class="landing-cta-primary">Pick another →</button>
          </form>
          {% else %}
          <form method="POST" action="/next_movie" style="display:inline;">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit" class="landing-cta-primary">Pick another →</button>
          </form>
          {% endif %}
          <a class="landing-cta-ghost" id="landing-see-this" href="{{ movie_url(landing_film) }}">See this film ↗</a>
        </div>
      {% else %}
        <h1 class="landing-headline" id="landing-headline" aria-live="polite">No films match these filters.</h1>
        <p class="landing-sub" id="landing-sub">Try removing one.</p>
        <div class="landing-actions">
          <a class="landing-cta-primary" href="/">Clear filters</a>
        </div>
      {% endif %}
      {#
        Pill aria-pressed reads from `url_filters` (the raw URL query-args dict the
        route passes to the template). active_filters is only used for the form
        hidden-input loop above — its keys are translated to the form schema
        ('genres[]', 'year_min', etc.) and not all URL params produce form entries
        (runtime is dropped because /filtered_movie has no runtime form key).
        Using url_filters here keeps the activation logic symmetric with the URL.
      #}
      <div class="landing-pills" id="landing-pills" role="group" aria-label="Quick filters">
        <button type="button" class="landing-pill" data-filter-key="genre" data-filter-value="Drama" aria-pressed="{{ 'true' if url_filters.get('genre') == 'Drama' else 'false' }}">Drama</button>
        <button type="button" class="landing-pill" data-filter-key="genre" data-filter-value="Comedy" aria-pressed="{{ 'true' if url_filters.get('genre') == 'Comedy' else 'false' }}">Comedy</button>
        <button type="button" class="landing-pill" data-filter-key="decade" data-filter-value="1990s" aria-pressed="{{ 'true' if url_filters.get('decade') == '1990s' else 'false' }}">1990s</button>
        <button type="button" class="landing-pill" data-filter-key="runtime" data-filter-value="lt120" aria-pressed="{{ 'true' if url_filters.get('runtime') == 'lt120' else 'false' }}">&lt; 120 min</button>
        <button type="button" class="landing-pill" data-filter-key="rating" data-filter-value="7plus" aria-pressed="{{ 'true' if url_filters.get('rating') == '7plus' else 'false' }}">7+ rating</button>
        {% if landing_film %}
        <a class="landing-pill-link" href="{{ movie_url(landing_film) }}">More filters →</a>
        {% endif %}
      </div>
    </div>
  </main>

  <script src="{{ url_for('static', filename='js/landing-pills.js') }}?v={{ config.get('CSS_VERSION', '1') }}" defer></script>

  {% include 'footer_modern.html' %}
</body>
</html>
```

- [ ] **Step 2: Unskip the template-dependent tests in `tests/web/test_routes_home.py`**

Find each `@pytest.mark.skip(reason="Requires template rewrite — unskip in Task 6")` and delete the line.

- [ ] **Step 3: Run the home route tests to verify they pass**

Run: `python3 -m pytest tests/web/test_routes_home.py -v -k "test_home_with"`
Expected: All 5 tests PASS.

- [ ] **Step 4: Visual smoke test**

Start the dev server: `python3 app.py`

In a browser:
- Visit `http://127.0.0.1:5000/` — should show side-by-side hero, no kicker, no side label, headline "A film you haven't seen. Every time you ask.", filter pills below CTAs.
- Visit `http://127.0.0.1:5000/?genre=Drama` — Drama pill should render in the active state with `×` indicator. Backdrop should be a drama film.
- Click "Pick another →" — should navigate to `/movie/<id>` (Drama film since the form posts to `/filtered_movie`).
- Resize browser to ≤ 767px wide — layout should stack, backdrop becomes square, page becomes scrollable.

Stop the dev server when done.

- [ ] **Step 5: Commit**

```bash
git add templates/home.html tests/web/test_routes_home.py
git commit -m "feat(landing): side-by-side hero template

Replaces the centered hero with a side-by-side layout — backdrop on
the left half, content stack on the right (kicker and side label
removed). The H1 is now the value-prop headline; the film title moves
to the credit corner.

Active filters in URL params render the matching pill in the active
state with × indicator and switch the primary CTA's form action from
/next_movie to /filtered_movie with hidden inputs mirroring the URL
state. When no film matches an active filter combination, the empty
state replaces the headline + subtitle and shows a Clear-filters link.

The 'More filters →' link routes to the inline filter UI on the
movie detail page (existing UX).

Loads landing-pills.js for the in-place reroll behavior added in the
next task."
```

---

## Task 7: Client-side JS — `landing-pills.js`

Vanilla JS module: pill click handlers, fetch `/api/landing-film`, swap DOM, `history.pushState`, popstate handler, reduced-motion respect.

**Files:**
- Create: `static/js/landing-pills.js`

- [ ] **Step 1: Create `static/js/landing-pills.js`**

```javascript
/**
 * Landing-page filter pills — in-place hero reroll.
 *
 * Wires click handlers on the pill buttons in the right column. On click,
 * toggles the corresponding URL param via History API and fetches a new
 * landing film from /api/landing-film matching the new filter combination.
 * Updates the backdrop, credit corner, "See this film" link href, and the
 * primary CTA form (re-targets between /next_movie and /filtered_movie
 * depending on whether any filters are active).
 *
 * Respects prefers-reduced-motion (skips the backdrop fade animations).
 *
 * No external dependencies.
 */

(() => {
  'use strict';

  const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // URL schema mappings — must mirror movies/landing_filter_url.py.
  const RUNTIME_VALID = new Set(['lt90', 'lt120', 'gt150']);
  const RATING_VALID = new Set(['6plus', '7plus', '8plus']);
  const DECADE_VALID = new Set(['1970s', '1980s', '1990s', '2000s', '2010s', '2020s']);

  const FORM_KEYS_BY_URL = {
    genre: ['genre'],
    decade: ['min_year', 'max_year'],
    runtime: ['max_runtime', 'min_runtime'],
    rating: ['min_rating'],
  };

  const root = document.getElementById('landing-pills');
  if (!root) return;

  const bg = document.getElementById('landing-bg');
  const credit = document.getElementById('landing-credit');
  const headline = document.getElementById('landing-headline');
  const sub = document.getElementById('landing-sub');
  const seeThisLink = document.getElementById('landing-see-this');
  const actions = document.querySelector('.landing-actions');

  // === URL state helpers ===

  function readActiveFilters() {
    const params = new URLSearchParams(window.location.search);
    const active = {};
    if (params.has('genre')) active.genre = params.get('genre');
    if (params.has('decade') && DECADE_VALID.has(params.get('decade'))) {
      active.decade = params.get('decade');
    }
    if (params.has('runtime') && RUNTIME_VALID.has(params.get('runtime'))) {
      active.runtime = params.get('runtime');
    }
    if (params.has('rating') && RATING_VALID.has(params.get('rating'))) {
      active.rating = params.get('rating');
    }
    return active;
  }

  function writeActiveFilters(active) {
    const params = new URLSearchParams();
    Object.keys(active).forEach((k) => params.set(k, active[k]));
    const qs = params.toString();
    const url = qs ? `?${qs}` : window.location.pathname;
    window.history.pushState({ active }, '', url);
  }

  // === Pill state UI ===

  function syncPillsToActive(active) {
    const pills = root.querySelectorAll('.landing-pill[data-filter-key]');
    pills.forEach((p) => {
      const key = p.dataset.filterKey;
      const value = p.dataset.filterValue;
      const isActive = active[key] === value;
      p.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
  }

  // === Form action sync ===

  function rewriteCtaForm(active, film) {
    if (!actions) return;
    if (!film) return; // Empty-state has no Pick-Another form to rewrite.

    const form = actions.querySelector('form');
    if (!form) return;

    // Compute form-schema hidden inputs from active URL state.
    const formInputs = {};
    if (active.genre) formInputs.genre = active.genre;
    if (active.decade) {
      const [, y0, y9] = active.decade.match(/^(\d{4})\D*$/) || [];
      // active.decade is like "1990s" — derive years client-side to match server.
      const yearsByDecade = {
        '1970s': [1970, 1979], '1980s': [1980, 1989], '1990s': [1990, 1999],
        '2000s': [2000, 2009], '2010s': [2010, 2019], '2020s': [2020, 2029],
      };
      const yrs = yearsByDecade[active.decade];
      if (yrs) {
        formInputs.min_year = String(yrs[0]);
        formInputs.max_year = String(yrs[1]);
      }
    }
    if (active.runtime === 'lt90') formInputs.max_runtime = '90';
    else if (active.runtime === 'lt120') formInputs.max_runtime = '120';
    else if (active.runtime === 'gt150') formInputs.min_runtime = '150';
    if (active.rating === '6plus') formInputs.min_rating = '6.0';
    else if (active.rating === '7plus') formInputs.min_rating = '7.0';
    else if (active.rating === '8plus') formInputs.min_rating = '8.0';

    const hasFilters = Object.keys(formInputs).length > 0;
    form.action = hasFilters ? '/filtered_movie' : '/next_movie';

    // Remove existing filter-input children (keep csrf_token).
    Array.from(form.querySelectorAll('input[type="hidden"]')).forEach((inp) => {
      if (inp.name !== 'csrf_token') inp.remove();
    });
    // Add fresh ones.
    Object.keys(formInputs).forEach((k) => {
      const inp = document.createElement('input');
      inp.type = 'hidden';
      inp.name = k;
      inp.value = formInputs[k];
      form.appendChild(inp);
    });
  }

  // === Empty state DOM ===

  function renderEmptyState() {
    if (headline) headline.textContent = 'No films match these filters.';
    if (sub) sub.textContent = 'Try removing one.';
    if (bg) {
      bg.style.backgroundImage = "url('/static/img/backdrop-placeholder.svg')";
    }
    if (credit) credit.style.display = 'none';
    if (seeThisLink) seeThisLink.style.display = 'none';
    // Replace primary CTA with Clear-filters link.
    if (actions) {
      const form = actions.querySelector('form');
      if (form) {
        const link = document.createElement('a');
        link.className = 'landing-cta-primary';
        link.href = '/';
        link.textContent = 'Clear filters';
        form.replaceWith(link);
      }
    }
  }

  // === Hydrate film into the page ===

  function renderFilm(film) {
    if (!film) {
      renderEmptyState();
      return;
    }
    if (bg) {
      bg.style.backgroundImage = `url('${film.backdrop_url}')`;
    }
    if (credit) {
      const titleAndYear = film.year
        ? `Film still: ${film.title} (${film.year})`
        : `Film still: ${film.title}`;
      credit.textContent = titleAndYear;
      credit.style.display = '';
    }
    if (seeThisLink) {
      // movie_url logic: prefer public_id, fall back to tconst.
      const slug = film.public_id || film.tconst;
      seeThisLink.href = `/movie/${slug}`;
      seeThisLink.style.display = '';
    }
    // Restore Pick-Another form if it was replaced by Clear-filters.
    if (actions && !actions.querySelector('form')) {
      const link = actions.querySelector('a.landing-cta-primary[href="/"]');
      if (link) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.style.display = 'inline';
        const csrf = document.createElement('input');
        csrf.type = 'hidden';
        csrf.name = 'csrf_token';
        // Re-use the value rendered by the server elsewhere if present.
        const existing = document.querySelector('input[name="csrf_token"]');
        csrf.value = existing ? existing.value : '';
        form.appendChild(csrf);
        const btn = document.createElement('button');
        btn.type = 'submit';
        btn.className = 'landing-cta-primary';
        btn.textContent = 'Pick another →';
        form.appendChild(btn);
        link.replaceWith(form);
      }
    }
    // Restore default headline / sub if we're coming out of empty state.
    if (headline && headline.textContent.startsWith('No films match')) {
      headline.innerHTML = "A film you haven't seen.<br>Every time you ask.";
    }
    if (sub && sub.textContent.startsWith('Try removing')) {
      sub.textContent = "Mark what you've seen. Filter the rest. Every pick is fresh.";
    }
  }

  // === Fetch + apply ===

  async function fetchAndApply(active) {
    const params = new URLSearchParams();
    Object.keys(active).forEach((k) => params.set(k, active[k]));
    const url = `/api/landing-film${params.toString() ? '?' + params.toString() : ''}`;

    if (!REDUCED_MOTION && bg) bg.classList.add('is-loading');

    try {
      const resp = await fetch(url, { headers: { Accept: 'application/json' } });
      if (resp.status === 204) {
        renderFilm(null);
      } else if (resp.ok) {
        const film = await resp.json();
        renderFilm(film);
      } else {
        // 4xx/5xx — leave the page unchanged.
        // eslint-disable-next-line no-console
        console.warn('landing-film fetch failed', resp.status);
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn('landing-film fetch error', err);
    } finally {
      if (!REDUCED_MOTION && bg) {
        // Tiny delay so the fade-out is perceivable.
        setTimeout(() => bg.classList.remove('is-loading'), 60);
      }
      rewriteCtaForm(active, true);
    }
  }

  // === Click handler ===

  root.addEventListener('click', (ev) => {
    const target = ev.target.closest('.landing-pill[data-filter-key]');
    if (!target) return;

    ev.preventDefault();
    const key = target.dataset.filterKey;
    const value = target.dataset.filterValue;

    const active = readActiveFilters();
    if (active[key] === value) {
      // Click on active pill — deactivate.
      delete active[key];
    } else {
      // Click on inactive pill — activate (replaces any prior value at this key).
      active[key] = value;
    }

    writeActiveFilters(active);
    syncPillsToActive(active);
    fetchAndApply(active);
  });

  // === Browser back/forward ===

  window.addEventListener('popstate', () => {
    const active = readActiveFilters();
    syncPillsToActive(active);
    fetchAndApply(active);
  });

  // === Initial state sync ===
  // Server already rendered the right pills in the active state and the
  // right CTA form. This is a defensive no-op for direct navigation.
  syncPillsToActive(readActiveFilters());
})();
```

- [ ] **Step 2: Manual smoke test**

Start the dev server: `python3 app.py`

In a browser at `http://127.0.0.1:5000/`:

- Click "Drama" — URL becomes `/?genre=Drama`, backdrop changes, Drama pill becomes active with `×`.
- Click "Comedy" — URL becomes `/?genre=Comedy`, Drama pill goes idle, Comedy pill goes active.
- Click "Comedy" again — URL drops `genre`, Comedy goes idle, backdrop changes to any film.
- Click "1990s" + "< 120 min" + "7+ rating" — URL accumulates params, backdrop is a 1990s film under 120 min with 7+ rating.
- Click each active pill once to clear all — URL becomes `/`, all pills idle.
- Try a combination that yields nothing (e.g., contrived: hit each pill rapidly until you get an empty match) — empty state should render.
- Use browser back button — URL walks back, active pills re-sync, backdrop re-fetches.
- In macOS System Settings → Accessibility → Display → Reduce Motion: enable. Refresh page. Click pills — backdrop changes without fade animation.

Stop the dev server.

- [ ] **Step 3: Commit**

```bash
git add static/js/landing-pills.js
git commit -m "feat(landing): client-side pill interactivity

Vanilla JS module wires click handlers on the filter pill row. On click,
toggles the corresponding URL param via History API and fetches a new
landing film from /api/landing-film matching the active filter set.
Updates backdrop, credit corner, See-this-film link, and re-targets the
primary CTA form between /next_movie and /filtered_movie depending on
whether any filters are active.

Empty state (204 from /api/landing-film) replaces headline + subtitle
and swaps the primary CTA to a Clear-filters link.

Browser back/forward walks the filter history correctly via popstate.
Respects prefers-reduced-motion by skipping the backdrop fade
transition. No external dependencies."
```

---

## Task 8: Final integration verification

Run the full test suite to make sure nothing else broke. Manually verify the validation checklist from the spec.

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS. (The repo's CI gate is 40% coverage on Python 3.11 / 3.12 — a focused PR that touches landing-page code shouldn't regress this.)

- [ ] **Step 2: Run lint and format**

Run: `black . --line-length 100`
Expected: No changes needed (or auto-format anything that drifted).

Run: `flake8 . --exclude=venv,node_modules`
Expected: No errors.

- [ ] **Step 3: Spec validation checklist**

Start the dev server: `python3 app.py`

Walk through each item from the spec's "Validation checklist":

- [ ] Visual: load `/` 10 times — 10 different films render
- [ ] Visual: load `/?genre=Drama` 10 times — 10 different drama films render
- [ ] Visual: load `/?genre=NotAGenre` — silently treated as no filter, renders any film
- [ ] Visual: load `/?genre=Drama&decade=1990s` — 1990s drama film
- [ ] Pill click: Drama (idle) → URL becomes `/?genre=Drama`, backdrop changes, Drama active with `×`
- [ ] Pill click: Comedy while Drama active → Drama goes idle, Comedy goes active
- [ ] Pill click: Drama (active) → URL drops `genre=Drama`, Drama goes idle
- [ ] Combine all four dimensions — backdrop matches, all four pills active
- [ ] Empty state: filter combo with no matches → headline becomes "No films match these filters.", primary CTA becomes "Clear filters"
- [ ] Browser back/forward: navigate Drama → Comedy → Drama via pill clicks, then use browser back twice — URL and active pills walk back through the history correctly
- [ ] Primary CTA with no filters → `/next_movie`; with filters → `/filtered_movie` with hidden inputs
- [ ] Secondary CTA "See this film ↗" routes to a valid `/movie/{public_id}` page
- [ ] "More filters →" routes to the inline filter UI on the movie detail page
- [ ] Mobile (375px width via responsive devtools) — backdrop is square, content stacks below, pills wrap, CTAs stack vertically, no horizontal overflow
- [ ] Reduced motion: System Settings toggle — Ken Burns frozen, fade-up frozen, backdrop reroll fade frozen
- [ ] Keyboard: Tab through navbar → primary CTA → secondary CTA → each pill → "More filters →" — all visible focus rings
- [ ] No console errors on the route, no 4xx/5xx for `/` or `/api/landing-film`, no broken image URLs on a sample of 20 picks

Stop the dev server when done.

- [ ] **Step 4: Final commit (verification only — no code changes)**

If any items above surfaced bugs, fix and commit those separately. If all green:

No code change for this task — verification is its own check. Skip the commit.

---

## Self-Review

**Spec coverage check:**

| Spec section | Implemented in |
|---|---|
| Side-by-side desktop layout | Task 5 (CSS), Task 6 (template) |
| Stacked mobile layout | Task 5 (mobile media query) |
| Headline + subtitle | Task 6 (template) |
| Filter strip with 5 pills + More-filters | Task 6 (template) + Task 7 (JS) |
| Sticky URL-backed filter persistence | Task 1 (translation), Task 4 (server render), Task 7 (client) |
| Active in-place reroll | Task 3 (JSON endpoint), Task 7 (JS) |
| Empty state | Task 6 (server-rendered) + Task 7 (client-rendered) |
| Mutual exclusion of Drama/Comedy | Task 7 (JS click handler `active[key] = value` replaces prior value at same key) |
| Personalization strip — REJECTED | Verified in Task 6 (template has no auth-conditional block) |
| Kicker removed | Task 5 (CSS removed), Task 6 (template has no kicker markup) |
| Side label removed | Task 5 (CSS removed), Task 6 (template has no side-label markup) |
| Visual grammar preserved (Bebas Neue, Ken Burns, grain, fade-up, credit corner) | Task 5 (CSS preserves) |
| CTAs unchanged labels (Pick another / See this film) | Task 6 (template) |
| Conditional form action (filtered/non-filtered) | Task 6 (template) + Task 7 (JS rewrite) |
| Fallback pool used only for unfiltered no-match | Task 4 (route handler) |
| `/api/landing-film` JSON endpoint | Task 3 |
| URL params validation (silent drop on invalid) | Task 1 |

All spec sections covered.

**Type/signature consistency:**

- `criteria_from_query_args(args) -> dict[str, Any]` — used identically in Task 1, Task 3, Task 4 ✓
- `active_filters_for_template(criteria) -> dict[str, str]` — keys match the template's iteration in Task 6 (`for k, v in active_filters.items()` produces `<input name="{{ k }}" value="{{ v }}">`) ✓
- `fetch_random_landing_film(pool, criteria=None)` — call signature consistent across Task 2 (definition), Task 3 (endpoint), Task 4 (home route) ✓
- The form-schema keys (`genre`, `min_year`, `max_year`, `max_runtime`, `min_rating`, `min_runtime`) returned by `active_filters_for_template` match the existing `/filtered_movie` route's expectations as verified in `movies/filter_parser.py:extract_movie_filter_criteria` ✓

**Placeholder scan:** No "TBD", "TODO", "FIXME", "implement later", "similar to Task N", or other placeholder language in any task. Every step has either complete code, an exact command, or a precise verification step.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-26-landing-page-discovery-redesign.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best when each task should be inspected in isolation.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Best when you want to see progress live.

Which approach?
