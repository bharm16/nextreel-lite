# Watched Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign `/watched` from a flat thumbnail grid into a browsable archive with editorial styling, server-side filtering/sorting, filter chips, and load-more pagination.

**Architecture:** Add `list_watched_filtered()` and `available_filter_chips()` to `WatchedStore` for server-side filtering/sorting/pagination. Update the route handler to accept filter/sort query params and return paginated JSON for "load more" requests. Rewrite the template with breathing header, sticky toolbar with filter chips, and redesigned poster grid. Replace the existing JS with a new `watched.js` for scroll-wash, filter chip toggling, load-more fetch, search, remove+undo, and toast.

**Tech Stack:** Python/Quart (backend), Jinja2 (templates), vanilla JS (client), CSS custom properties via `input.css` (styling), MySQL (data)

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Modify | `movies/watched_store.py` | Add `list_watched_filtered()` and `available_filter_chips()` methods |
| Modify | `nextreel/web/route_services.py` | Simplify `WatchedListPresenter` — drop stats, add genre extraction |
| Modify | `nextreel/web/routes/watched.py` | Accept filter/sort/page params, JSON response for load-more |
| Rewrite | `templates/watched_list.html` | New page structure: header, toolbar, grid, empty state |
| Rewrite | `templates/_watched_card.html` | Simplified poster card with scale+bar hover |
| Rewrite | `static/css/input.css` (lines 1115-1453) | New watched page CSS |
| Create | `static/js/watched.js` | Scroll wash, filter chips, load-more, search, remove+undo |
| Delete | `static/js/watched-list.js` | Replaced by `watched.js` |
| Modify | `tests/movies/test_watched_store.py` | Tests for new query methods |
| Modify | `tests/web/test_watched_route_delegation.py` | Tests for filter/sort params, JSON load-more |

---

### Task 1: Add `list_watched_filtered()` to WatchedStore

**Files:**
- Modify: `movies/watched_store.py`
- Test: `tests/movies/test_watched_store.py`

This method replaces `list_watched()` as the primary query for the watched page. It accepts sort, decade filter, rating filter, and genre filter, and builds a dynamic SQL query with parameterized WHERE clauses.

- [ ] **Step 1: Write the failing tests**

Add to `tests/movies/test_watched_store.py`:

```python
# ---------------------------------------------------------------------------
# list_watched_filtered
# ---------------------------------------------------------------------------


async def test_list_watched_filtered_default_sort_recent(mock_db_pool):
    """list_watched_filtered() defaults to ORDER BY w.watched_at DESC."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0)

    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.watched_at DESC" in query


async def test_list_watched_filtered_sort_title_az(mock_db_pool):
    """list_watched_filtered() with sort='title_asc' orders A-Z."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered("user-1", sort="title_asc", limit=60, offset=0)

    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY c.primaryTitle ASC" in query


async def test_list_watched_filtered_sort_title_za(mock_db_pool):
    """list_watched_filtered() with sort='title_desc' orders Z-A."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered("user-1", sort="title_desc", limit=60, offset=0)

    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY c.primaryTitle DESC" in query


async def test_list_watched_filtered_sort_year_desc(mock_db_pool):
    """list_watched_filtered() with sort='year_desc' orders newest first."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered("user-1", sort="year_desc", limit=60, offset=0)

    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY c.startYear DESC" in query


async def test_list_watched_filtered_sort_rating_desc(mock_db_pool):
    """list_watched_filtered() with sort='rating_desc' orders highest first."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered("user-1", sort="rating_desc", limit=60, offset=0)

    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY" in query
    assert "rating" in query.lower() or "averageRating" in query


async def test_list_watched_filtered_decade_filter(mock_db_pool):
    """list_watched_filtered() with decades=['2020'] filters to 2020-2029."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered(
        "user-1", sort="recent", limit=60, offset=0, decades=["2020"]
    )

    query = mock_db_pool.execute.call_args[0][0]
    params = mock_db_pool.execute.call_args[0][1]
    assert "c.startYear >=" in query or "startYear BETWEEN" in query or "c.startYear >=" in query
    assert 2020 in params
    assert 2029 in params


async def test_list_watched_filtered_multiple_decades(mock_db_pool):
    """list_watched_filtered() with decades=['2020','2010'] uses OR within decade."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered(
        "user-1", sort="recent", limit=60, offset=0, decades=["2020", "2010"]
    )

    params = mock_db_pool.execute.call_args[0][1]
    assert 2020 in params
    assert 2029 in params
    assert 2010 in params
    assert 2019 in params


async def test_list_watched_filtered_rating_filter(mock_db_pool):
    """list_watched_filtered() with rating_min filters by tmdb_rating/averageRating."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered(
        "user-1", sort="recent", limit=60, offset=0, rating_min=8.0, rating_max=10.0
    )

    query = mock_db_pool.execute.call_args[0][0]
    params = mock_db_pool.execute.call_args[0][1]
    assert "averageRating" in query or "rating" in query.lower()


async def test_list_watched_filtered_genre_filter(mock_db_pool):
    """list_watched_filtered() with genres=['Horror'] filters by genre substring."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered(
        "user-1", sort="recent", limit=60, offset=0, genres=["Horror"]
    )

    query = mock_db_pool.execute.call_args[0][0]
    assert "genres" in query.lower()


async def test_list_watched_filtered_combined_filters(mock_db_pool):
    """list_watched_filtered() applies decade AND genre filters together."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered(
        "user-1",
        sort="recent",
        limit=60,
        offset=0,
        decades=["2020"],
        genres=["Horror"],
    )

    query = mock_db_pool.execute.call_args[0][0]
    assert "genres" in query.lower()
    assert "startYear" in query


async def test_list_watched_filtered_returns_rows(mock_db_pool):
    """list_watched_filtered() returns the row list from DB."""
    rows = [
        {"tconst": "tt1", "primaryTitle": "Test", "startYear": 2024, "genres": "Drama"},
    ]
    mock_db_pool.execute.return_value = rows
    store = _make_store(mock_db_pool)

    result = await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0)

    assert result == rows


async def test_list_watched_filtered_returns_empty_on_none(mock_db_pool):
    """list_watched_filtered() returns [] when DB returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    result = await store.list_watched_filtered("user-1", sort="recent", limit=60, offset=0)

    assert result == []


async def test_list_watched_filtered_passes_limit_offset(mock_db_pool):
    """list_watched_filtered() passes limit and offset as query params."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered("user-1", sort="recent", limit=30, offset=60)

    params = mock_db_pool.execute.call_args[0][1]
    # limit and offset are the last two params
    assert params[-2] == 30
    assert params[-1] == 60


async def test_list_watched_filtered_invalid_sort_falls_back(mock_db_pool):
    """list_watched_filtered() with invalid sort falls back to recent."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    await store.list_watched_filtered("user-1", sort="invalid_sort", limit=60, offset=0)

    query = mock_db_pool.execute.call_args[0][0]
    assert "ORDER BY w.watched_at DESC" in query
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_watched_store.py -k "list_watched_filtered" -v`
Expected: FAIL — `WatchedStore` has no `list_watched_filtered` attribute

- [ ] **Step 3: Implement `list_watched_filtered()`**

Add to `movies/watched_store.py`, after the existing `list_watched` method:

```python
_SORT_MAP = {
    "recent": "w.watched_at DESC",
    "title_asc": "c.primaryTitle ASC",
    "title_desc": "c.primaryTitle DESC",
    "year_desc": "c.startYear DESC, c.primaryTitle ASC",
    "rating_desc": "c.averageRating DESC, c.primaryTitle ASC",
}

async def list_watched_filtered(
    self,
    user_id: str,
    *,
    sort: str = "recent",
    limit: int = 60,
    offset: int = 0,
    decades: list[str] | None = None,
    rating_min: float | None = None,
    rating_max: float | None = None,
    genres: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return watched movies with optional filtering and sorting.

    Filters combine as AND across categories, OR within a category.
    """
    where_clauses = ["w.user_id = %s"]
    params: list[Any] = [user_id]

    # Decade filter — OR within category
    if decades:
        decade_parts = []
        for decade_str in decades:
            try:
                decade_start = int(decade_str)
            except (TypeError, ValueError):
                continue
            decade_parts.append("(c.startYear >= %s AND c.startYear <= %s)")
            params.extend([decade_start, decade_start + 9])
        if decade_parts:
            where_clauses.append("(" + " OR ".join(decade_parts) + ")")

    # Rating filter
    if rating_min is not None:
        where_clauses.append("c.averageRating >= %s")
        params.append(rating_min)
    if rating_max is not None:
        where_clauses.append("c.averageRating <= %s")
        params.append(rating_max)

    # Genre filter — OR within category (FIND_IN_SET for CSV genres)
    if genres:
        genre_parts = []
        for genre in genres:
            genre_parts.append("FIND_IN_SET(%s, c.genres) > 0")
            params.append(genre)
        if genre_parts:
            where_clauses.append("(" + " OR ".join(genre_parts) + ")")

    order_by = _SORT_MAP.get(sort, _SORT_MAP["recent"])

    where_sql = " AND ".join(where_clauses)
    params.extend([limit, offset])

    rows = await self.db_pool.execute(
        f"""
        SELECT sub.tconst, sub.watched_at,
               sub.primaryTitle, sub.startYear, sub.genres, sub.slug,
               sub.averageRating,
               p.payload_json
        FROM (
            SELECT w.tconst, w.watched_at,
                   c.primaryTitle, c.startYear, c.genres, c.slug,
                   c.averageRating
            FROM user_watched_movies w
            LEFT JOIN movie_candidates c ON w.tconst = c.tconst
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
        ) sub
        LEFT JOIN movie_projection p ON sub.tconst = p.tconst
        """,
        params,
        fetch="all",
    )
    return rows if rows else []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_watched_store.py -k "list_watched_filtered" -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add movies/watched_store.py tests/movies/test_watched_store.py
git commit -m "feat(watched): add list_watched_filtered with sort/filter support"
```

---

### Task 2: Add `count_filtered()` and `available_filter_chips()` to WatchedStore

**Files:**
- Modify: `movies/watched_store.py`
- Test: `tests/movies/test_watched_store.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/movies/test_watched_store.py`:

```python
# ---------------------------------------------------------------------------
# count_filtered
# ---------------------------------------------------------------------------


async def test_count_filtered_no_filters(mock_db_pool):
    """count_filtered() without filters returns total watched count."""
    mock_db_pool.execute.return_value = {"cnt": 100}
    store = _make_store(mock_db_pool)

    result = await store.count_filtered("user-1")

    assert result == 100


async def test_count_filtered_with_decade(mock_db_pool):
    """count_filtered() with decade filter includes decade WHERE clause."""
    mock_db_pool.execute.return_value = {"cnt": 42}
    store = _make_store(mock_db_pool)

    result = await store.count_filtered("user-1", decades=["2020"])

    assert result == 42
    query = mock_db_pool.execute.call_args[0][0]
    assert "startYear" in query


async def test_count_filtered_returns_zero_on_none(mock_db_pool):
    """count_filtered() returns 0 when DB returns None."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    result = await store.count_filtered("user-1")

    assert result == 0


# ---------------------------------------------------------------------------
# available_filter_chips
# ---------------------------------------------------------------------------


async def test_available_filter_chips_returns_decades(mock_db_pool):
    """available_filter_chips() returns decade labels from startYear values."""
    mock_db_pool.execute.return_value = [
        {"startYear": 2024, "genres": "Drama", "averageRating": 7.5},
        {"startYear": 2015, "genres": "Horror,Comedy", "averageRating": 8.2},
        {"startYear": 2023, "genres": "Drama", "averageRating": 5.0},
    ]
    store = _make_store(mock_db_pool)

    chips = await store.available_filter_chips("user-1")

    assert "2020s" in chips["decades"]
    assert "2010s" in chips["decades"]


async def test_available_filter_chips_returns_genres(mock_db_pool):
    """available_filter_chips() returns unique genres from CSV genre column."""
    mock_db_pool.execute.return_value = [
        {"startYear": 2024, "genres": "Drama,Horror", "averageRating": 7.5},
        {"startYear": 2015, "genres": "Horror,Comedy", "averageRating": 8.2},
    ]
    store = _make_store(mock_db_pool)

    chips = await store.available_filter_chips("user-1")

    assert "Drama" in chips["genres"]
    assert "Horror" in chips["genres"]
    assert "Comedy" in chips["genres"]


async def test_available_filter_chips_returns_rating_tiers(mock_db_pool):
    """available_filter_chips() returns rating tiers that have >=1 film."""
    mock_db_pool.execute.return_value = [
        {"startYear": 2024, "genres": "Drama", "averageRating": 8.5},
        {"startYear": 2024, "genres": "Drama", "averageRating": 7.0},
        {"startYear": 2024, "genres": "Drama", "averageRating": 4.0},
    ]
    store = _make_store(mock_db_pool)

    chips = await store.available_filter_chips("user-1")

    assert {"label": "8+", "min": 8.0, "max": 10.0} in chips["ratings"]
    assert {"label": "6\u20138", "min": 6.0, "max": 7.99} in chips["ratings"]
    assert {"label": "<6", "min": 0.0, "max": 5.99} in chips["ratings"]


async def test_available_filter_chips_empty_watched(mock_db_pool):
    """available_filter_chips() returns empty lists when user has no films."""
    mock_db_pool.execute.return_value = []
    store = _make_store(mock_db_pool)

    chips = await store.available_filter_chips("user-1")

    assert chips["decades"] == []
    assert chips["genres"] == []
    assert chips["ratings"] == []


async def test_available_filter_chips_skips_null_genres(mock_db_pool):
    """available_filter_chips() ignores rows with None/empty genres."""
    mock_db_pool.execute.return_value = [
        {"startYear": 2024, "genres": None, "averageRating": 7.0},
        {"startYear": 2024, "genres": "", "averageRating": 7.0},
        {"startYear": 2024, "genres": "Drama", "averageRating": 7.0},
    ]
    store = _make_store(mock_db_pool)

    chips = await store.available_filter_chips("user-1")

    assert chips["genres"] == ["Drama"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_watched_store.py -k "count_filtered or available_filter_chips" -v`
Expected: FAIL — methods don't exist

- [ ] **Step 3: Implement both methods**

Add to `movies/watched_store.py`:

```python
async def count_filtered(
    self,
    user_id: str,
    *,
    decades: list[str] | None = None,
    rating_min: float | None = None,
    rating_max: float | None = None,
    genres: list[str] | None = None,
) -> int:
    """Return count of watched movies matching the given filters."""
    where_clauses = ["w.user_id = %s"]
    params: list[Any] = [user_id]

    if decades:
        decade_parts = []
        for decade_str in decades:
            try:
                decade_start = int(decade_str)
            except (TypeError, ValueError):
                continue
            decade_parts.append("(c.startYear >= %s AND c.startYear <= %s)")
            params.extend([decade_start, decade_start + 9])
        if decade_parts:
            where_clauses.append("(" + " OR ".join(decade_parts) + ")")

    if rating_min is not None:
        where_clauses.append("c.averageRating >= %s")
        params.append(rating_min)
    if rating_max is not None:
        where_clauses.append("c.averageRating <= %s")
        params.append(rating_max)

    if genres:
        genre_parts = []
        for genre in genres:
            genre_parts.append("FIND_IN_SET(%s, c.genres) > 0")
            params.append(genre)
        if genre_parts:
            where_clauses.append("(" + " OR ".join(genre_parts) + ")")

    where_sql = " AND ".join(where_clauses)

    row = await self.db_pool.execute(
        f"""
        SELECT COUNT(*) AS cnt
        FROM user_watched_movies w
        LEFT JOIN movie_candidates c ON w.tconst = c.tconst
        WHERE {where_sql}
        """,
        params,
        fetch="one",
    )
    return row["cnt"] if row else 0

async def available_filter_chips(self, user_id: str) -> dict[str, list]:
    """Return available filter chip options based on the user's watched data.

    Returns dict with keys: decades, genres, ratings.
    Each contains only values that have >= 1 matching film.
    """
    rows = await self.db_pool.execute(
        """
        SELECT c.startYear, c.genres, c.averageRating
        FROM user_watched_movies w
        LEFT JOIN movie_candidates c ON w.tconst = c.tconst
        WHERE w.user_id = %s AND c.tconst IS NOT NULL
        """,
        [user_id],
        fetch="all",
    )
    if not rows:
        return {"decades": [], "genres": [], "ratings": []}

    decade_set: set[str] = set()
    genre_set: set[str] = set()
    has_8_plus = False
    has_6_8 = False
    has_under_6 = False

    for row in rows:
        # Decades
        year = row.get("startYear")
        if year:
            try:
                decade = (int(year) // 10) * 10
                decade_set.add(f"{decade}s")
            except (TypeError, ValueError):
                pass

        # Genres
        genres_csv = row.get("genres")
        if genres_csv and isinstance(genres_csv, str):
            for g in genres_csv.split(","):
                g = g.strip()
                if g:
                    genre_set.add(g)

        # Ratings
        rating = row.get("averageRating")
        if rating is not None:
            try:
                r = float(rating)
                if r >= 8.0:
                    has_8_plus = True
                elif r >= 6.0:
                    has_6_8 = True
                else:
                    has_under_6 = True
            except (TypeError, ValueError):
                pass

    rating_tiers = []
    if has_8_plus:
        rating_tiers.append({"label": "8+", "min": 8.0, "max": 10.0})
    if has_6_8:
        rating_tiers.append({"label": "6\u20138", "min": 6.0, "max": 7.99})
    if has_under_6:
        rating_tiers.append({"label": "<6", "min": 0.0, "max": 5.99})

    return {
        "decades": sorted(decade_set, reverse=True),
        "genres": sorted(genre_set),
        "ratings": rating_tiers,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_watched_store.py -k "count_filtered or available_filter_chips" -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add movies/watched_store.py tests/movies/test_watched_store.py
git commit -m "feat(watched): add count_filtered and available_filter_chips"
```

---

### Task 3: Update the route handler for filtering, sorting, and JSON load-more

**Files:**
- Modify: `nextreel/web/routes/watched.py`
- Modify: `nextreel/web/route_services.py`
- Test: `tests/web/test_watched_route_delegation.py`

- [ ] **Step 1: Write the failing test for the JSON load-more endpoint**

Add to `tests/web/test_watched_route_delegation.py`:

```python
async def test_watched_list_json_returns_html_and_metadata(mock_app_client):
    """GET /watched?page=2 with Accept: application/json returns JSON with html, total, has_more."""
    response = await mock_app_client.get(
        "/watched?page=2&per_page=60",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    data = await response.get_json()
    assert "html" in data
    assert "total" in data
    assert "has_more" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_watched_route_delegation.py -k "json_returns" -v`
Expected: FAIL

- [ ] **Step 3: Update the route handler**

Replace the `watched_list_page` function in `nextreel/web/routes/watched.py` with:

```python
def _parse_filter_params(args) -> dict:
    """Extract filter parameters from request query string."""
    result = {}

    decades_raw = args.get("decades", "")
    if decades_raw:
        result["decades"] = [d.strip().rstrip("s") for d in decades_raw.split(",") if d.strip()]

    rating_tier = args.get("rating", "")
    if rating_tier == "8+":
        result["rating_min"] = 8.0
        result["rating_max"] = 10.0
    elif rating_tier == "6-8":
        result["rating_min"] = 6.0
        result["rating_max"] = 7.99
    elif rating_tier == "<6":
        result["rating_min"] = 0.0
        result["rating_max"] = 5.99

    genres_raw = args.get("genres", "")
    if genres_raw:
        result["genres"] = [g.strip() for g in genres_raw.split(",") if g.strip()]

    return result


_VALID_SORTS = {"recent", "title_asc", "title_desc", "year_desc", "rating_desc"}


@bp.route("/watched")
async def watched_list_page():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    user_id = _current_user_id()
    services = _services()
    watched_store = services.movie_manager.watched_store

    page, per_page, offset = _parse_watched_pagination(request.args)
    sort = request.args.get("sort", "recent")
    if sort not in _VALID_SORTS:
        sort = "recent"
    filter_params = _parse_filter_params(request.args)

    from quart import session as quart_session

    enrichment_pending = quart_session.get("letterboxd_enrichment_pending", False)

    raw_rows, total_count, filter_chips = await asyncio.gather(
        watched_store.list_watched_filtered(
            user_id, sort=sort, limit=per_page, offset=offset, **filter_params
        ),
        watched_store.count_filtered(user_id, **filter_params),
        watched_store.available_filter_chips(user_id),
    )

    view_model = _watched_list_presenter.build(
        raw_rows=raw_rows,
        total_count=total_count,
        page=page,
        per_page=per_page,
        now=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    has_more = (offset + per_page) < total_count

    if _wants_json_response():
        from quart import render_template as rt

        html_parts = [
            await rt("_watched_card.html", movie=movie) for movie in view_model.movies
        ]
        return jsonify(
            {
                "html": "".join(html_parts),
                "total": total_count,
                "has_more": has_more,
                "page": page,
            }
        )

    return await render_template(
        "watched_list.html",
        movies=view_model.movies,
        total=view_model.total,
        filter_chips=filter_chips,
        has_more=has_more,
        pagination=view_model.pagination,
        enrichment_pending=enrichment_pending,
        current_sort=sort,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/web/test_watched_route_delegation.py -v`
Expected: PASS (existing tests may need minor adjustment if they relied on old `stats` template var — check and fix)

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/watched.py nextreel/web/route_services.py tests/web/test_watched_route_delegation.py
git commit -m "feat(watched): route accepts filter/sort params, returns JSON for load-more"
```

---

### Task 4: Rewrite the watched page template

**Files:**
- Rewrite: `templates/watched_list.html`
- Rewrite: `templates/_watched_card.html`

- [ ] **Step 1: Rewrite `_watched_card.html`**

Replace the entire file with:

```html
{% set href = url_for('main.movie_detail', tconst=movie.tconst) %}
<div
  class="watched-card"
  role="listitem"
  data-tconst="{{ movie.tconst }}"
  data-title="{{ movie.title }}"
  data-year="{{ movie.year if movie.year else '' }}"
  data-rating="{{ movie.tmdb_rating }}"
  data-watched="{{ movie.watched_at }}"
  data-search="{{ movie.title|lower }} {{ movie.year or '' }}"
>
  <a class="watched-poster-link" href="{{ href }}">
    <img
      class="watched-poster"
      src="{{ movie.poster_url }}"
      alt="{{ movie.title }}{% if movie.year %} ({{ movie.year }}){% endif %}"
      loading="lazy"
    />
    <div class="watched-card-bar" aria-hidden="true">
      <span class="watched-card-title">{{ movie.title }}</span>
      <span class="watched-card-year">{{ movie.year if movie.year else '' }}</span>
    </div>
  </a>
  <button
    type="button"
    class="watched-remove"
    data-tconst="{{ movie.tconst }}"
    aria-label="Remove {{ movie.title }} from watched"
  >
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
      <path d="M18 6L6 18M6 6l12 12"/>
    </svg>
  </button>
</div>
```

- [ ] **Step 2: Rewrite `watched_list.html`**

Replace the entire file with the new breathing-header + sticky-toolbar + grid layout. Full template:

```html
{% from "macros.html" import pick_movie_button with context %}
<!DOCTYPE html>
<html lang="en" class="scroll-smooth">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Watched – Nextreel</title>
  <meta name="description" content="Your watched films on Nextreel." />
  <meta name="csrf-token" content="{{ csrf_token() }}" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Merriweather:ital,wght@0,300;0,400;0,700;1,400&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/output.css') }}?v={{ config.get('CSS_VERSION', '1') }}">
  <script src="{{ url_for('static', filename='js/theme-boot.js') }}?v={{ config.get('CSS_VERSION', '1') }}"></script>
  <style>
    body { font-family: var(--font-sans, 'DM Sans', system-ui, sans-serif); background: var(--color-bg); color: var(--color-text); }
  </style>
</head>
<body class="antialiased">
  <a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow">Skip to content</a>

  {% include 'navbar_modern.html' %}

  {% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
  <div class="max-w-5xl mx-auto px-4 pt-4">
    {% for category, message in messages %}
    <div role="alert" class="mb-4 rounded-lg border px-4 py-3 text-sm font-medium
      {% if category == 'error' %}border-red-400 bg-red-50 text-red-800 dark:border-red-600 dark:bg-red-900/30 dark:text-red-200
      {% elif category == 'warning' %}border-yellow-400 bg-yellow-50 text-yellow-800 dark:border-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-200
      {% elif category == 'success' %}border-green-400 bg-green-50 text-green-800 dark:border-green-600 dark:bg-green-900/30 dark:text-green-200
      {% else %}border-blue-400 bg-blue-50 text-blue-800 dark:border-blue-600 dark:bg-blue-900/30 dark:text-blue-200{% endif %}">
      {{ message }}
    </div>
    {% endfor %}
  </div>
  {% endif %}
  {% endwith %}

  {% if session.get('letterboxd_unmatched') %}
  <div class="max-w-5xl mx-auto px-4">
    <details class="mb-4 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800/50">
      <summary class="cursor-pointer px-4 py-2 text-sm font-medium text-gray-600 dark:text-gray-300">
        Show unmatched films ({{ session['letterboxd_unmatched']|length }})
      </summary>
      <ul class="px-4 pb-3 text-xs text-gray-500 dark:text-gray-400 columns-2 gap-4">
        {% for film in session['letterboxd_unmatched'] %}
        <li>{{ film }}</li>
        {% endfor %}
      </ul>
    </details>
  </div>
  {% set _ = session.pop('letterboxd_unmatched', None) %}
  {% endif %}

  <main id="main" aria-label="Watched films archive">
    {% if movies|length == 0 and not (filter_chips is defined and filter_chips) %}
      {# ── Empty state ── #}
      <div class="watched-empty">
        <h1 class="watched-empty-title">Your film journey starts here</h1>
        <hr class="watched-empty-rule" />
        <form method="POST" action="{{ url_for('main.import_letterboxd') }}" enctype="multipart/form-data" class="watched-empty-import">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
          <label class="watched-empty-cta watched-empty-cta--primary">
            <svg class="watched-letterboxd-icon" viewBox="0 0 500 500" aria-hidden="true"><circle cx="108" cy="250" r="108" fill="#00e054"/><circle cx="250" cy="250" r="108" fill="#40bcf4"/><circle cx="392" cy="250" r="108" fill="#ff8000"/><path d="M179 170.6a108 108 0 000 158.8 108 108 0 000-158.8z" fill="#84d548"/><path d="M179 329.4a108 108 0 000-158.8 108 108 0 000 158.8z" fill="#84d548"/><path d="M321 170.6a108 108 0 010 158.8 108 108 0 010-158.8z" fill="#a8e0f4"/><path d="M321 329.4a108 108 0 010-158.8 108 108 0 010 158.8z" fill="#a8e0f4"/></svg>
            Import from Letterboxd
            <input type="file" name="letterboxd_csv" accept=".csv" required class="sr-only" onchange="this.form.submit()" />
          </label>
        </form>
        <a href="/" class="watched-empty-cta watched-empty-cta--secondary">Pick a Movie</a>
      </div>
    {% else %}
      {# ── Header ── #}
      <header class="watched-header">
        <h1 class="watched-title">Watched</h1>
        <p class="watched-subtitle"><em>{{ total }} films and counting</em></p>
      </header>

      {# ── Toolbar ── #}
      <div class="watched-toolbar" id="watched-toolbar">
        <div class="watched-toolbar-row">
          <div class="watched-search-wrap">
            <svg class="watched-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input
              type="search"
              id="watched-search"
              class="watched-search"
              placeholder="Search films..."
              aria-label="Search watched films"
              autocomplete="off"
            />
          </div>
          <div class="watched-toolbar-right">
            <a href="javascript:void(0)" class="watched-letterboxd-link" title="Import from Letterboxd" id="watched-letterboxd-trigger">
              <svg class="watched-letterboxd-icon" viewBox="0 0 500 500" aria-label="Import from Letterboxd"><circle cx="108" cy="250" r="108" fill="#00e054"/><circle cx="250" cy="250" r="108" fill="#40bcf4"/><circle cx="392" cy="250" r="108" fill="#ff8000"/><path d="M179 170.6a108 108 0 000 158.8 108 108 0 000-158.8z" fill="#84d548"/><path d="M179 329.4a108 108 0 000-158.8 108 108 0 000 158.8z" fill="#84d548"/><path d="M321 170.6a108 108 0 010 158.8 108 108 0 010-158.8z" fill="#a8e0f4"/><path d="M321 329.4a108 108 0 010-158.8 108 108 0 010 158.8z" fill="#a8e0f4"/></svg>
            </a>
            <select id="watched-sort" class="watched-sort" aria-label="Sort watched films">
              <option value="recent" {% if current_sort == 'recent' %}selected{% endif %}>Recent</option>
              <option value="title_asc" {% if current_sort == 'title_asc' %}selected{% endif %}>A – Z</option>
              <option value="title_desc" {% if current_sort == 'title_desc' %}selected{% endif %}>Z – A</option>
              <option value="year_desc" {% if current_sort == 'year_desc' %}selected{% endif %}>Year</option>
              <option value="rating_desc" {% if current_sort == 'rating_desc' %}selected{% endif %}>Rating</option>
            </select>
          </div>
        </div>

        {# ── Filter chips ── #}
        {% if filter_chips %}
        <div class="watched-chips" role="group" aria-label="Filter by">
          <button type="button" class="watched-chip watched-chip--active" data-filter="all" aria-pressed="true">All</button>
          {% for decade in filter_chips.decades %}
          <button type="button" class="watched-chip" data-filter="decade" data-value="{{ decade.rstrip('s') }}" aria-pressed="false">{{ decade }}</button>
          {% endfor %}
          {% for tier in filter_chips.ratings %}
          <button type="button" class="watched-chip" data-filter="rating" data-value="{{ tier.label }}" data-min="{{ tier.min }}" data-max="{{ tier.max }}" aria-pressed="false">{{ tier.label }}</button>
          {% endfor %}
          {% for genre in filter_chips.genres %}
          <button type="button" class="watched-chip" data-filter="genre" data-value="{{ genre }}" aria-pressed="false">{{ genre }}</button>
          {% endfor %}
        </div>
        {% endif %}

        <div class="watched-filter-count" id="watched-filter-count" aria-live="polite" hidden>
          Showing <span id="watched-showing">0</span> of {{ total }} films
        </div>
      </div>

      {# ── Grid ── #}
      <section class="watched-grid" id="watched-grid" role="list">
        {% for movie in movies %}
          {% include '_watched_card.html' %}
        {% endfor %}
      </section>

      {# ── Load more / End mark ── #}
      <div class="watched-grid-footer" id="watched-grid-footer">
        {% if has_more %}
        <button type="button" class="watched-load-more" id="watched-load-more"
                data-page="2" data-per-page="{{ pagination.per_page }}">
          Load more
        </button>
        {% else %}
        <div class="watched-end-mark">
          <hr class="watched-end-rule" />
          <p class="watched-end-text">That's all {{ total }}</p>
        </div>
        {% endif %}
      </div>
    {% endif %}
  </main>

  <div id="watched-toast" class="watched-toast" role="status" aria-live="polite" hidden></div>

  {# ── Hidden Letterboxd import form ── #}
  <form id="watched-letterboxd-form" method="POST" action="{{ url_for('main.import_letterboxd') }}" enctype="multipart/form-data" hidden>
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
    <input type="file" name="letterboxd_csv" id="watched-letterboxd-file" accept=".csv" />
  </form>

  <script src="{{ url_for('static', filename='js/watched.js') }}?v={{ config.get('CSS_VERSION', '1') }}" defer></script>

  {% if enrichment_pending %}
  <script src="{{ url_for('static', filename='js/watched-enrichment-progress.js') }}?v={{ config.get('CSS_VERSION', '1') }}" defer></script>
  {% endif %}

  {% include 'footer_modern.html' %}
</body>
</html>
```

- [ ] **Step 3: Verify template renders without errors**

Run: `python3 app.py` and visit `http://127.0.0.1:5000/watched` (must be logged in)
Expected: Page loads (may look unstyled until CSS is updated in next task)

- [ ] **Step 4: Commit**

```bash
git add templates/watched_list.html templates/_watched_card.html
git commit -m "feat(watched): rewrite templates with breathing header and filter chips"
```

---

### Task 5: Rewrite watched page CSS

**Files:**
- Modify: `static/css/input.css` (replace lines 1115-1453)

- [ ] **Step 1: Replace the watched CSS block**

Replace the entire watched page CSS block (from `.watched-header` through the `prefers-reduced-motion` media query around line 1453) with:

```css
  /* ── Watched page: Header ── */
  .watched-header {
    padding: 5rem 1.5rem 0;
    margin-bottom: 2.5rem;
  }
  .watched-title {
    font-family: var(--font-serif);
    font-size: 2.25rem;
    font-weight: 300;
    color: var(--color-text);
    margin: 0 0 0.6rem;
    line-height: 1.1;
    letter-spacing: -0.02em;
  }
  .watched-subtitle {
    font-family: var(--font-serif);
    font-size: 1rem;
    font-weight: 300;
    color: var(--color-text-muted);
    margin: 0;
    line-height: 1.4;
  }

  /* ── Watched page: Toolbar ── */
  .watched-toolbar {
    position: sticky;
    top: 0;
    z-index: 20;
    padding: 1.25rem 1.5rem 0;
    border-top: 1px solid var(--color-border);
    background: transparent;
    transition: background-color 200ms ease;
  }
  .watched-toolbar.is-scrolled {
    background: rgba(17, 17, 17, 0.4);
  }
  :root:not(.dark) .watched-toolbar.is-scrolled {
    background: rgba(245, 244, 240, 0.4);
  }
  .watched-toolbar-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1rem;
  }
  .watched-search-wrap {
    position: relative;
    width: min(280px, 50%);
  }
  .watched-search-icon {
    position: absolute;
    left: 0;
    top: 50%;
    transform: translateY(-50%);
    width: 14px;
    height: 14px;
    color: var(--color-text-muted);
    pointer-events: none;
  }
  .watched-search {
    width: 100%;
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--color-border);
    padding: 0.5rem 0 0.5rem 1.5rem;
    font-family: var(--font-sans);
    font-size: 0.8rem;
    color: var(--color-text);
    outline: none;
    transition: border-color 200ms ease;
  }
  .watched-search::placeholder {
    color: var(--color-text-muted);
  }
  .watched-search:focus {
    border-bottom-color: var(--color-accent);
  }
  .watched-search:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }
  .watched-toolbar-right {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  .watched-letterboxd-link {
    color: var(--color-text-muted);
    transition: color 200ms ease;
    display: flex;
    align-items: center;
  }
  .watched-letterboxd-link:hover {
    color: var(--color-accent);
  }
  .watched-letterboxd-icon {
    width: 18px;
    height: 18px;
  }
  .watched-sort {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sharp);
    background: transparent;
    color: var(--color-text);
    font-family: var(--font-sans);
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 0.5rem 2rem 0.5rem 0.75rem;
    cursor: pointer;
    appearance: none;
    -webkit-appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12' fill='none' stroke='%236b6860' stroke-width='1.5'%3E%3Cpath d='M3 5l3 3 3-3'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 0.55rem center;
    background-size: 12px;
  }
  .watched-sort:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }

  /* ── Filter chips ── */
  .watched-chips {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
    padding-bottom: 1.25rem;
  }
  .watched-chip {
    font-family: var(--font-sans);
    font-size: 0.65rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.25rem 0.65rem;
    border-radius: 2px;
    border: 1px solid var(--color-border);
    background: transparent;
    color: var(--color-text-muted);
    cursor: pointer;
    transition: background-color 150ms ease, color 150ms ease, border-color 150ms ease;
  }
  .watched-chip:hover {
    border-color: var(--color-accent);
    color: var(--color-text);
  }
  .watched-chip:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }
  .watched-chip--active,
  .watched-chip[aria-pressed="true"] {
    background: var(--color-accent);
    border-color: var(--color-accent);
    color: #fff;
  }
  .watched-filter-count {
    font-family: var(--font-sans);
    font-size: 0.7rem;
    color: var(--color-text-muted);
    padding-bottom: 0.75rem;
  }

  /* ── Poster grid ── */
  .watched-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 1.5rem;
    padding: 2rem 1.5rem 0;
  }
  .watched-card {
    position: relative;
    display: block;
    aspect-ratio: 2 / 3;
    border-radius: 2px;
    background: var(--color-surface);
    overflow: hidden;
    text-decoration: none;
    color: inherit;
  }
  .watched-poster-link {
    position: absolute;
    inset: 0;
    display: block;
    text-decoration: none;
    color: inherit;
    border-radius: inherit;
    overflow: hidden;
  }
  .watched-poster-link:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }
  .watched-poster {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    transition: transform 200ms ease;
    transform-origin: center;
  }
  .watched-card:hover .watched-poster,
  .watched-card:focus-within .watched-poster {
    transform: scale(1.03);
  }
  .watched-card-bar {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 0.65rem 0.75rem;
    background: rgba(17, 17, 17, 0.92);
    border-top: 1px solid var(--color-accent);
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    transform: translateY(100%);
    transition: transform 200ms ease;
  }
  .watched-card:hover .watched-card-bar,
  .watched-card:focus-within .watched-card-bar {
    transform: translateY(0);
  }
  .watched-card-title {
    font-family: var(--font-serif);
    font-size: 0.85rem;
    font-weight: 300;
    color: var(--color-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 70%;
  }
  .watched-card-year {
    font-family: var(--font-sans);
    font-size: 0.65rem;
    font-weight: 500;
    text-transform: uppercase;
    color: var(--color-text-muted);
    flex-shrink: 0;
  }
  .watched-remove {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.5);
    border: none;
    color: var(--color-text-muted);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    opacity: 0;
    transition: opacity 200ms ease, color 200ms ease;
    padding: 0;
    z-index: 2;
  }
  .watched-remove svg {
    width: 12px;
    height: 12px;
  }
  .watched-card:hover .watched-remove,
  .watched-card:focus-within .watched-remove,
  .watched-remove:focus-visible {
    opacity: 1;
  }
  .watched-remove:hover {
    color: var(--color-accent);
  }
  .watched-remove:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
    opacity: 1;
  }

  /* ── Grid footer: load more + end mark ── */
  .watched-grid-footer {
    padding: 2.5rem 1.5rem;
    text-align: center;
  }
  .watched-load-more {
    font-family: var(--font-sans);
    font-size: 0.75rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-muted);
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.5rem 1rem;
    transition: color 200ms ease;
  }
  .watched-load-more:hover {
    color: var(--color-accent);
  }
  .watched-load-more:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }
  .watched-end-mark {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
  }
  .watched-end-rule {
    border: none;
    border-top: 1px solid var(--color-border);
    width: 2rem;
    margin: 0;
  }
  .watched-end-text {
    font-family: var(--font-sans);
    font-size: 0.7rem;
    color: var(--color-text-muted);
    margin: 0;
  }

  /* ── Toast ── */
  .watched-toast {
    position: fixed;
    bottom: 1.5rem;
    left: 50%;
    transform: translateX(-50%);
    background: var(--color-surface);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    padding: 0.6rem 1rem;
    border-radius: var(--radius-sharp);
    font-family: var(--font-sans);
    font-size: 0.8rem;
    z-index: 1000;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .watched-toast-undo {
    color: var(--color-accent);
    background: none;
    border: none;
    cursor: pointer;
    font-family: var(--font-sans);
    font-size: 0.8rem;
    font-weight: 600;
    padding: 0;
  }
  .watched-toast-undo:hover {
    text-decoration: underline;
  }
  .watched-toast-undo:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }

  /* ── Empty state ── */
  .watched-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 60vh;
    padding: 2rem 1.5rem;
    text-align: center;
  }
  .watched-empty-title {
    font-family: var(--font-serif);
    font-size: 1.4rem;
    font-weight: 300;
    color: var(--color-text);
    margin: 0 0 1.5rem;
  }
  .watched-empty-rule {
    border: none;
    border-top: 1px solid var(--color-accent);
    width: 2rem;
    margin: 0 auto 1.5rem;
  }
  .watched-empty-import {
    margin-bottom: 1rem;
  }
  .watched-empty-cta {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    width: 100%;
    max-width: 280px;
    padding: 0.75rem 1.5rem;
    font-family: var(--font-sans);
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    text-decoration: none;
    border-radius: var(--radius-sharp);
    cursor: pointer;
    transition: background-color 200ms ease, color 200ms ease;
  }
  .watched-empty-cta--primary {
    background: var(--color-accent);
    color: #fff;
    border: none;
  }
  .watched-empty-cta--primary:hover {
    filter: brightness(1.1);
  }
  .watched-empty-cta--secondary {
    background: transparent;
    color: var(--color-text-muted);
    border: none;
  }
  .watched-empty-cta--secondary:hover {
    color: var(--color-accent);
  }

  /* ── Responsive ── */
  @media (max-width: 640px) {
    .watched-header { padding: 4rem 1rem 0; margin-bottom: 2rem; }
    .watched-title { font-size: 1.75rem; }
    .watched-toolbar { padding: 1rem 1rem 0; }
    .watched-grid {
      padding: 1.5rem 1rem 0;
      gap: 1rem;
    }
    .watched-search-wrap { width: 60%; }
    .watched-grid-footer { padding: 2rem 1rem; }
  }

  @media (prefers-reduced-motion: reduce) {
    .watched-poster,
    .watched-card-bar,
    .watched-remove,
    .watched-search,
    .watched-toolbar,
    .watched-chip,
    .watched-load-more {
      transition: none !important;
    }
  }
```

- [ ] **Step 2: Rebuild Tailwind CSS**

Run: `npm run build-css`
Expected: `static/css/output.css` regenerated without errors

- [ ] **Step 3: Verify visually**

Run: `python3 app.py` and visit `http://127.0.0.1:5000/watched`
Expected: Breathing header with subtitle, hairline separator, toolbar with search/sort/chips, looser poster grid. Hover should show scale + bottom bar. (JS interactions won't work yet — next task.)

- [ ] **Step 4: Commit**

```bash
git add static/css/input.css
git commit -m "feat(watched): redesign CSS with breathing header and editorial grid"
```

---

### Task 6: Create `watched.js` — all client-side interactivity

**Files:**
- Create: `static/js/watched.js`
- Delete: `static/js/watched-list.js`

- [ ] **Step 1: Create `static/js/watched.js`**

```javascript
(function () {
  "use strict";

  var grid = document.getElementById("watched-grid");
  if (!grid) return;

  var toolbar = document.getElementById("watched-toolbar");
  var searchInput = document.getElementById("watched-search");
  var sortSelect = document.getElementById("watched-sort");
  var loadMoreBtn = document.getElementById("watched-load-more");
  var gridFooter = document.getElementById("watched-grid-footer");
  var toastEl = document.getElementById("watched-toast");
  var filterCountEl = document.getElementById("watched-filter-count");
  var showingEl = document.getElementById("watched-showing");
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.getAttribute("content") || "" : "";
  var letterboxdTrigger = document.getElementById("watched-letterboxd-trigger");
  var letterboxdFile = document.getElementById("watched-letterboxd-file");
  var letterboxdForm = document.getElementById("watched-letterboxd-form");
  var toastTimer = null;
  var searchTimer = null;

  // ── Scroll wash on toolbar ──
  if (toolbar) {
    var scrollThreshold = toolbar.offsetTop;
    function onScroll() {
      if (window.scrollY > scrollThreshold) {
        toolbar.classList.add("is-scrolled");
      } else {
        toolbar.classList.remove("is-scrolled");
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // ── Client-side search (title filter) ──
  if (searchInput) {
    searchInput.addEventListener("input", function () {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        var query = (searchInput.value || "").trim().toLowerCase();
        var cards = grid.querySelectorAll(".watched-card");
        var visible = 0;
        cards.forEach(function (card) {
          var match = !query || (card.dataset.search || "").indexOf(query) !== -1;
          card.style.display = match ? "" : "none";
          if (match) visible++;
        });
        if (query && filterCountEl) {
          filterCountEl.hidden = false;
          if (showingEl) showingEl.textContent = visible;
        } else if (filterCountEl) {
          filterCountEl.hidden = true;
        }
      }, 100);
    });
  }

  // ── Sort change → full page reload with params ──
  if (sortSelect) {
    sortSelect.addEventListener("change", function () {
      var url = new URL(window.location.href);
      url.searchParams.set("sort", sortSelect.value);
      url.searchParams.delete("page");
      window.location.href = url.toString();
    });
  }

  // ── Filter chips ──
  var chips = document.querySelectorAll(".watched-chip");
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var filterType = chip.dataset.filter;

      if (filterType === "all") {
        // Reset all filters
        window.location.href = window.location.pathname +
          (sortSelect ? "?sort=" + sortSelect.value : "");
        return;
      }

      var url = new URL(window.location.href);
      url.searchParams.delete("page");

      var paramName = filterType === "decade" ? "decades"
                    : filterType === "rating" ? "rating"
                    : "genres";

      if (filterType === "rating") {
        // Rating is single-select
        var currentRating = url.searchParams.get("rating");
        if (currentRating === chip.dataset.value) {
          url.searchParams.delete("rating");
        } else {
          url.searchParams.set("rating", chip.dataset.value);
        }
      } else {
        // Decades and genres are multi-select (comma-separated)
        var current = url.searchParams.get(paramName);
        var values = current ? current.split(",") : [];
        var val = chip.dataset.value;
        var idx = values.indexOf(val);
        if (idx > -1) {
          values.splice(idx, 1);
        } else {
          values.push(val);
        }
        if (values.length > 0) {
          url.searchParams.set(paramName, values.join(","));
        } else {
          url.searchParams.delete(paramName);
        }
      }

      window.location.href = url.toString();
    });
  });

  // Mark active chips based on current URL params
  (function markActiveChips() {
    var url = new URL(window.location.href);
    var hasAnyFilter = url.searchParams.has("decades") ||
                       url.searchParams.has("rating") ||
                       url.searchParams.has("genres");

    chips.forEach(function (chip) {
      var filterType = chip.dataset.filter;
      if (filterType === "all") {
        chip.setAttribute("aria-pressed", hasAnyFilter ? "false" : "true");
        if (hasAnyFilter) {
          chip.classList.remove("watched-chip--active");
        } else {
          chip.classList.add("watched-chip--active");
        }
        return;
      }

      var paramName = filterType === "decade" ? "decades"
                    : filterType === "rating" ? "rating"
                    : "genres";
      var paramVal = url.searchParams.get(paramName) || "";
      var isActive = false;

      if (filterType === "rating") {
        isActive = paramVal === chip.dataset.value;
      } else {
        var vals = paramVal ? paramVal.split(",") : [];
        isActive = vals.indexOf(chip.dataset.value) > -1;
      }

      chip.setAttribute("aria-pressed", isActive ? "true" : "false");
      if (isActive) {
        chip.classList.add("watched-chip--active");
      } else {
        chip.classList.remove("watched-chip--active");
      }
    });
  })();

  // ── Load more ──
  if (loadMoreBtn) {
    loadMoreBtn.addEventListener("click", function () {
      var page = parseInt(loadMoreBtn.dataset.page, 10);
      var perPage = parseInt(loadMoreBtn.dataset.perPage, 10) || 60;
      loadMoreBtn.textContent = "Loading...";
      loadMoreBtn.disabled = true;

      var url = new URL(window.location.href);
      url.searchParams.set("page", page);
      url.searchParams.set("per_page", perPage);

      fetch(url.toString(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data.html) {
            grid.insertAdjacentHTML("beforeend", data.html);
          }
          if (data.has_more) {
            loadMoreBtn.dataset.page = page + 1;
            loadMoreBtn.textContent = "Load more";
            loadMoreBtn.disabled = false;
          } else {
            gridFooter.innerHTML =
              '<div class="watched-end-mark">' +
              '<hr class="watched-end-rule" />' +
              '<p class="watched-end-text">That\u2019s all ' + data.total + "</p>" +
              "</div>";
          }
        })
        .catch(function () {
          loadMoreBtn.textContent = "Load more";
          loadMoreBtn.disabled = false;
          showToast("Couldn\u2019t load more films. Try again.");
        });
    });
  }

  // ── Remove + Undo ──
  var lastRemoved = null;

  function showToast(message, undoCallback) {
    if (!toastEl) return;
    if (toastTimer) clearTimeout(toastTimer);

    if (undoCallback) {
      toastEl.innerHTML = "";
      var span = document.createElement("span");
      span.textContent = message;
      toastEl.appendChild(span);
      var undoBtn = document.createElement("button");
      undoBtn.className = "watched-toast-undo";
      undoBtn.textContent = "Undo";
      undoBtn.addEventListener("click", function () {
        undoCallback();
        toastEl.hidden = true;
        if (toastTimer) clearTimeout(toastTimer);
      });
      toastEl.appendChild(undoBtn);
      undoBtn.focus();
    } else {
      toastEl.textContent = message;
    }

    toastEl.hidden = false;
    toastTimer = setTimeout(function () {
      toastEl.hidden = true;
      lastRemoved = null;
    }, 5000);
  }

  grid.addEventListener("click", function (event) {
    var button = event.target.closest(".watched-remove");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();

    var card = button.closest(".watched-card");
    var tconst = button.dataset.tconst;
    var title = card ? card.dataset.title : "";
    if (!card || !tconst) return;

    // Animate out
    card.style.transition = "opacity 200ms ease, transform 200ms ease";
    card.style.opacity = "0";
    card.style.transform = "scale(0.95)";

    // Capture position for undo
    var nextSibling = card.nextElementSibling;
    var cardHtml = card.outerHTML;

    setTimeout(function () {
      card.remove();

      // Send remove request
      fetch("/watched/remove/" + encodeURIComponent(tconst), {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken, Accept: "application/json" },
        credentials: "same-origin",
      }).catch(function (err) {
        console.error("Failed to remove:", err);
      });

      lastRemoved = { tconst: tconst, html: cardHtml, nextSibling: nextSibling };

      showToast("Removed from watched", function () {
        // Undo: re-add to DOM and re-add on server
        var tmp = document.createElement("div");
        tmp.innerHTML = lastRemoved.html;
        var restored = tmp.firstElementChild;
        restored.style.opacity = "1";
        restored.style.transform = "";
        if (lastRemoved.nextSibling) {
          grid.insertBefore(restored, lastRemoved.nextSibling);
        } else {
          grid.appendChild(restored);
        }
        // Re-add on server
        fetch("/watched/add/" + encodeURIComponent(lastRemoved.tconst), {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken, Accept: "application/json" },
          credentials: "same-origin",
        }).catch(function (err) {
          console.error("Failed to undo remove:", err);
        });
        lastRemoved = null;
      });
    }, 200);
  });

  // ── Letterboxd import trigger ──
  if (letterboxdTrigger && letterboxdFile && letterboxdForm) {
    letterboxdTrigger.addEventListener("click", function (e) {
      e.preventDefault();
      letterboxdFile.click();
    });
    letterboxdFile.addEventListener("change", function () {
      if (letterboxdFile.files.length > 0) {
        letterboxdForm.submit();
      }
    });
  }

  // ── Enrichment progress card sync ──
  window.addEventListener("nextreel:watched-cards-added", function () {
    // Cards added by enrichment polling — no action needed,
    // they are already in the DOM from the progress script.
  });
})();
```

- [ ] **Step 2: Delete the old JS file**

```bash
rm static/js/watched-list.js
```

- [ ] **Step 3: Verify interactivity**

Run: `python3 app.py` and visit `http://127.0.0.1:5000/watched`
Expected:
- Toolbar gets wash effect on scroll
- Search filters cards client-side
- Sort dropdown reloads page with `?sort=` param
- Filter chips reload page with filter params
- "Load more" appends cards via JSON fetch
- Remove button animates out card and shows undo toast
- Letterboxd icon triggers file picker

- [ ] **Step 4: Commit**

```bash
git add static/js/watched.js
git rm static/js/watched-list.js
git commit -m "feat(watched): new JS with scroll wash, filter chips, load-more, undo toast"
```

---

### Task 7: Run full test suite and fix any breakage

**Files:**
- Various test files as needed

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: Check for any test failures caused by the template/route changes

- [ ] **Step 2: Fix any failing tests**

Common breakage points:
- Tests that check for `stats` template variable (removed — replaced by `total`)
- Tests that check for `watched-list.js` script tag (renamed to `watched.js`)
- Tests that expect `list_watched()` to be called (now `list_watched_filtered()`)
- Tests checking the response HTML structure

Fix each failing test to match the new template structure.

- [ ] **Step 3: Run tests again to confirm green**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add -A tests/
git commit -m "fix(tests): update watched tests for redesigned page"
```

---

### Task 8: Visual QA and polish

**Files:**
- Various CSS/template tweaks as needed

- [ ] **Step 1: Test dark mode**

Visit `/watched` in dark mode. Verify:
- Header subtitle is italic, muted
- Toolbar wash appears on scroll (dark wash)
- Filter chips show accent color when active
- Poster grid has adequate gaps
- Hover shows scale + bottom bar with accent border
- Remove icon appears on hover, accent on icon hover
- Toast appears at bottom center

- [ ] **Step 2: Test light mode**

Toggle to light mode. Verify:
- All tokens swap correctly
- Toolbar scroll wash uses light wash color
- Text contrast passes (muted text still readable)

- [ ] **Step 3: Test mobile viewport**

Resize to ~375px width. Verify:
- Grid drops to 2 columns
- Gaps reduce to 1rem
- Search input and sort dropdown are usable
- Filter chips wrap correctly
- "Load more" button is tappable

- [ ] **Step 4: Test empty state**

Log in as a user with no watched films (or temporarily clear the list). Verify:
- Centered empty state with "Your film journey starts here"
- Accent-colored rule
- Letterboxd import button with icon
- "Pick a Movie" link

- [ ] **Step 5: Test keyboard navigation**

Tab through the page. Verify:
- Filter chips show focus ring
- Poster cards are focusable
- Remove button appears on focus-within
- Sort dropdown is keyboard-accessible
- Toast undo button is focusable

- [ ] **Step 6: Fix any issues found and commit**

```bash
git add -A
git commit -m "fix(watched): visual QA polish and accessibility fixes"
```
