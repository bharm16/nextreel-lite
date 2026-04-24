# Letterboxd CSV Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to upload their Letterboxd `watched.csv` export to bulk-import watched films into nextreel, matched by title+year against `movie_candidates`.

**Architecture:** New `movies/letterboxd_import.py` module handles CSV parsing, title normalization, and DB matching. `WatchedStore` gets a `add_bulk()` method. A new POST route on the watched blueprint handles the upload. The watched list template gets a collapsible import section.

**Tech Stack:** Python CSV stdlib, Quart file uploads (`request.files`), aiomysql parameterized queries, existing `WatchedStore` + `db_pool.execute()` patterns.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `movies/letterboxd_import.py` | Create | CSV parsing, title normalization, DB matching |
| `movies/watched_store.py` | Modify (add method after line 51) | New `add_bulk()` for multi-value INSERT |
| `nextreel/web/routes/watched.py` | Modify (add route after line 70) | POST `/watched/import-letterboxd` handler |
| `templates/watched_list.html` | Modify (add import section) | Upload form UI |
| `tests/movies/test_letterboxd_import.py` | Create | Tests for parsing, normalization, matching |
| `tests/movies/test_watched_store.py` | Modify (add tests) | Tests for `add_bulk()` |

---

### Task 1: Title Normalization + CSV Parser

**Files:**
- Create: `tests/movies/test_letterboxd_import.py`
- Create: `movies/letterboxd_import.py`

- [ ] **Step 1: Write failing tests for `normalize_title`**

```python
# tests/movies/test_letterboxd_import.py
"""Tests for movies.letterboxd_import — Letterboxd CSV import logic."""

from __future__ import annotations

import io
import pytest

from movies.letterboxd_import import normalize_title, parse_watched_csv


class TestNormalizeTitle:
    def test_lowercase(self):
        assert normalize_title("GoodFellas") == "goodfellas"

    def test_en_dash_to_hyphen(self):
        assert normalize_title("Episode I \u2013 The Phantom Menace") == "episode i - the phantom menace"

    def test_em_dash_to_hyphen(self):
        assert normalize_title("Something \u2014 Else") == "something - else"

    def test_collapse_whitespace(self):
        assert normalize_title("The   Grand   Budapest") == "the grand budapest"

    def test_preserves_meaningful_punctuation(self):
        assert normalize_title("(500) Days of Summer") == "(500) days of summer"

    def test_preserves_colons(self):
        assert normalize_title("Star Wars: Episode I") == "star wars: episode i"

    def test_empty_string(self):
        assert normalize_title("") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_letterboxd_import.py::TestNormalizeTitle -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'movies.letterboxd_import'`

- [ ] **Step 3: Implement `normalize_title`**

```python
# movies/letterboxd_import.py
"""Letterboxd CSV import: parsing, normalization, and DB matching."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field


def normalize_title(title: str) -> str:
    """Normalize a film title for matching.

    Lowercase, replace en/em dashes with hyphens, collapse whitespace.
    """
    t = title.lower()
    t = t.replace("\u2013", "-")  # en-dash
    t = t.replace("\u2014", "-")  # em-dash
    t = re.sub(r"\s+", " ", t).strip()
    return t
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_letterboxd_import.py::TestNormalizeTitle -v`
Expected: all 7 PASS

- [ ] **Step 5: Write failing tests for `parse_watched_csv`**

Append to `tests/movies/test_letterboxd_import.py`:

```python
class TestParseWatchedCsv:
    def test_valid_csv(self):
        csv_text = "Date,Name,Year,Letterboxd URI\n2021-01-20,Inception,2010,https://boxd.it/1skk\n"
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert result == [{"name": "Inception", "year": 2010}]

    def test_multiple_rows(self):
        csv_text = (
            "Date,Name,Year,Letterboxd URI\n"
            "2021-01-20,Inception,2010,https://boxd.it/1skk\n"
            "2021-01-20,Tenet,2020,https://boxd.it/leq4\n"
        )
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert len(result) == 2
        assert result[0]["name"] == "Inception"
        assert result[1]["name"] == "Tenet"

    def test_missing_name_column_raises(self):
        csv_text = "Date,Title,Year,URI\n2021-01-20,Inception,2010,x\n"
        with pytest.raises(ValueError, match="Name"):
            parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))

    def test_missing_year_column_raises(self):
        csv_text = "Date,Name,Released,URI\n2021-01-20,Inception,2010,x\n"
        with pytest.raises(ValueError, match="Year"):
            parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))

    def test_skips_rows_with_non_integer_year(self):
        csv_text = "Date,Name,Year,Letterboxd URI\n2021-01-20,Inception,abc,x\n2021-01-20,Tenet,2020,x\n"
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert len(result) == 1
        assert result[0]["name"] == "Tenet"

    def test_skips_rows_with_empty_name(self):
        csv_text = "Date,Name,Year,Letterboxd URI\n2021-01-20,,2010,x\n2021-01-20,Tenet,2020,x\n"
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert len(result) == 1

    def test_empty_csv_body(self):
        csv_text = "Date,Name,Year,Letterboxd URI\n"
        result = parse_watched_csv(io.BytesIO(csv_text.encode("utf-8")))
        assert result == []
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_letterboxd_import.py::TestParseWatchedCsv -v`
Expected: FAIL — `parse_watched_csv` not yet implemented

- [ ] **Step 7: Implement `parse_watched_csv`**

Append to `movies/letterboxd_import.py`:

```python
def parse_watched_csv(file_stream: io.BufferedIOBase) -> list[dict]:
    """Parse a Letterboxd watched.csv export.

    Args:
        file_stream: binary file-like object (e.g. from request.files).

    Returns:
        List of ``{"name": str, "year": int}`` dicts.

    Raises:
        ValueError: if required columns (Name, Year) are missing.
    """
    text = io.TextIOWrapper(file_stream, encoding="utf-8")
    reader = csv.DictReader(text)

    if reader.fieldnames is None:
        raise ValueError("Empty CSV file")
    fields = set(reader.fieldnames)
    if "Name" not in fields:
        raise ValueError("Missing required column: Name")
    if "Year" not in fields:
        raise ValueError("Missing required column: Year")

    films = []
    for row in reader:
        name = (row.get("Name") or "").strip()
        year_raw = (row.get("Year") or "").strip()
        if not name or not year_raw:
            continue
        try:
            year = int(year_raw)
        except ValueError:
            continue
        films.append({"name": name, "year": year})

    return films
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_letterboxd_import.py -v`
Expected: all 14 PASS

- [ ] **Step 9: Commit**

```bash
git add movies/letterboxd_import.py tests/movies/test_letterboxd_import.py
git commit -m "feat: add Letterboxd CSV parser and title normalization"
```

---

### Task 2: DB Matching — `match_films()`

**Files:**
- Modify: `tests/movies/test_letterboxd_import.py` (add tests)
- Modify: `movies/letterboxd_import.py` (add `match_films`, `MatchResult`)

- [ ] **Step 1: Write failing tests for `match_films`**

Append to `tests/movies/test_letterboxd_import.py`:

```python
from unittest.mock import AsyncMock

from movies.letterboxd_import import match_films, MatchResult


class TestMatchFilms:
    async def test_exact_match(self, mock_db_pool):
        mock_db_pool.execute.return_value = [
            {"tconst": "tt0137523", "primaryTitle": "Fight Club", "startYear": 1999},
        ]
        films = [{"name": "Fight Club", "year": 1999}]

        result = await match_films(mock_db_pool, films)

        assert len(result.matched) == 1
        assert result.matched[0] == "tt0137523"
        assert result.unmatched == []
        assert result.total == 1

    async def test_normalized_match(self, mock_db_pool):
        """Film with en-dash in Letterboxd matches hyphen in DB."""
        mock_db_pool.execute.return_value = [
            {"tconst": "tt0120915", "primaryTitle": "Star Wars: Episode I - The Phantom Menace", "startYear": 1999},
        ]
        films = [{"name": "Star Wars: Episode I \u2013 The Phantom Menace", "year": 1999}]

        result = await match_films(mock_db_pool, films)

        assert len(result.matched) == 1
        assert result.matched[0] == "tt0120915"

    async def test_unmatched_films(self, mock_db_pool):
        mock_db_pool.execute.return_value = []
        films = [{"name": "Nonexistent Movie", "year": 2099}]

        result = await match_films(mock_db_pool, films)

        assert result.matched == []
        assert len(result.unmatched) == 1
        assert result.unmatched[0] == {"name": "Nonexistent Movie", "year": 2099}

    async def test_mixed_matched_and_unmatched(self, mock_db_pool):
        mock_db_pool.execute.return_value = [
            {"tconst": "tt0137523", "primaryTitle": "Fight Club", "startYear": 1999},
        ]
        films = [
            {"name": "Fight Club", "year": 1999},
            {"name": "Unknown Film", "year": 2050},
        ]

        result = await match_films(mock_db_pool, films)

        assert len(result.matched) == 1
        assert len(result.unmatched) == 1
        assert result.total == 2

    async def test_empty_input(self, mock_db_pool):
        result = await match_films(mock_db_pool, [])

        assert result.matched == []
        assert result.unmatched == []
        assert result.total == 0
        mock_db_pool.execute.assert_not_awaited()

    async def test_query_uses_parameterized_placeholders(self, mock_db_pool):
        mock_db_pool.execute.return_value = []
        films = [{"name": "Inception", "year": 2010}]

        await match_films(mock_db_pool, films)

        call_args = mock_db_pool.execute.call_args
        query = call_args[0][0]
        assert "%s" in query
        assert "LOWER" in query
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_letterboxd_import.py::TestMatchFilms -v`
Expected: FAIL — `cannot import name 'match_films'`

- [ ] **Step 3: Implement `match_films` and `MatchResult`**

Add to `movies/letterboxd_import.py` (after existing code):

```python
from logging_config import get_logger

logger = get_logger(__name__)

_MATCH_BATCH_SIZE = 200


@dataclass
class MatchResult:
    """Result of matching Letterboxd films against the DB."""

    matched: list[str] = field(default_factory=list)  # tconst values
    unmatched: list[dict] = field(default_factory=list)  # {name, year} dicts
    total: int = 0


async def match_films(db_pool, films: list[dict]) -> MatchResult:
    """Match (name, year) pairs against movie_candidates by normalized title.

    Args:
        db_pool: database connection pool with ``execute()`` method.
        films: list of ``{"name": str, "year": int}`` dicts.

    Returns:
        MatchResult with matched tconsts and unmatched film dicts.
    """
    result = MatchResult(total=len(films))
    if not films:
        return result

    # Build lookup keyed by (normalized_title, year) -> original film dict
    pending = {}
    for f in films:
        key = (normalize_title(f["name"]), f["year"])
        pending[key] = f

    # Query in batches
    for i in range(0, len(films), _MATCH_BATCH_SIZE):
        batch_films = films[i : i + _MATCH_BATCH_SIZE]
        batch_keys = [(normalize_title(f["name"]), f["year"]) for f in batch_films]

        conditions = []
        params = []
        for norm_title, year in batch_keys:
            conditions.append(
                "(LOWER(REPLACE(REPLACE(primaryTitle, '\u2013', '-'), '\u2014', '-')) = %s"
                " AND startYear = %s)"
            )
            params.extend([norm_title, year])

        query = (
            "SELECT tconst, primaryTitle, startYear "
            "FROM movie_candidates "
            "WHERE " + " OR ".join(conditions)
        )

        rows = await db_pool.execute(query, params, fetch="all")
        if not rows:
            continue

        for row in rows:
            key = (normalize_title(row["primaryTitle"]), row["startYear"])
            if key in pending:
                result.matched.append(row["tconst"])
                del pending[key]

    result.unmatched = list(pending.values())
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_letterboxd_import.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add movies/letterboxd_import.py tests/movies/test_letterboxd_import.py
git commit -m "feat: add Letterboxd film matching against movie_candidates"
```

---

### Task 3: `WatchedStore.add_bulk()`

**Files:**
- Modify: `tests/movies/test_watched_store.py` (add tests)
- Modify: `movies/watched_store.py:51` (add method after `add()`)

- [ ] **Step 1: Write failing tests for `add_bulk`**

Append to `tests/movies/test_watched_store.py`:

```python
# ---------------------------------------------------------------------------
# add_bulk
# ---------------------------------------------------------------------------


async def test_add_bulk_executes_multi_value_insert(mock_db_pool):
    """add_bulk() builds a multi-value INSERT with ON DUPLICATE KEY."""
    mock_db_pool.execute.return_value = None
    store = _make_store(mock_db_pool)

    count = await store.add_bulk("user-1", ["tt0000001", "tt0000002", "tt0000003"])

    assert count == 3
    call_args = mock_db_pool.execute.call_args
    query = call_args[0][0]
    assert "INSERT INTO user_watched_movies" in query
    assert "ON DUPLICATE KEY UPDATE" in query
    assert query.count("(%s, %s, %s)") == 3


async def test_add_bulk_empty_list_returns_zero(mock_db_pool):
    """add_bulk() with empty list does nothing."""
    store = _make_store(mock_db_pool)

    count = await store.add_bulk("user-1", [])

    assert count == 0
    mock_db_pool.execute.assert_not_awaited()


async def test_add_bulk_invalidates_cache(mock_db_pool):
    """add_bulk() invalidates watched cache after insert."""
    mock_db_pool.execute.return_value = None
    mock_cache = AsyncMock()
    store = _make_store(mock_db_pool)
    store.attach_cache(mock_cache)

    await store.add_bulk("user-1", ["tt0000001"])

    mock_cache.delete.assert_awaited_once()
```

- [ ] **Step 2: Add missing import to test file**

At the top of `tests/movies/test_watched_store.py`, ensure `AsyncMock` is imported:

```python
from unittest.mock import AsyncMock
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_watched_store.py::test_add_bulk_executes_multi_value_insert -v`
Expected: FAIL — `WatchedStore has no attribute 'add_bulk'`

- [ ] **Step 4: Implement `add_bulk` in `WatchedStore`**

Add after the `add()` method (after line 51) in `movies/watched_store.py`:

```python
    async def add_bulk(self, user_id: str, tconsts: list[str]) -> int:
        """Mark multiple movies as watched in a single bulk insert.

        Uses multi-value INSERT with ON DUPLICATE KEY for idempotency.
        Processes in chunks of 500 to avoid query size limits.

        Returns:
            Number of tconsts processed (not necessarily newly inserted).
        """
        if not tconsts:
            return 0

        now = utcnow()
        chunk_size = 500
        total = 0

        for i in range(0, len(tconsts), chunk_size):
            chunk = tconsts[i : i + chunk_size]
            placeholders = ", ".join(["(%s, %s, %s)"] * len(chunk))
            params = []
            for tc in chunk:
                params.extend([user_id, tc, now])

            await self.db_pool.execute(
                f"INSERT INTO user_watched_movies (user_id, tconst, watched_at) "
                f"VALUES {placeholders} "
                f"ON DUPLICATE KEY UPDATE watched_at = VALUES(watched_at)",
                params,
                fetch="none",
            )
            total += len(chunk)

        await self._invalidate_cache(user_id)
        logger.info("Bulk-added %d watched movies for user %s", total, user_id)
        return total
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_watched_store.py -v`
Expected: all PASS (including existing tests)

- [ ] **Step 6: Commit**

```bash
git add movies/watched_store.py tests/movies/test_watched_store.py
git commit -m "feat: add WatchedStore.add_bulk() for multi-value insert"
```

---

### Task 4: Import Route

**Files:**
- Modify: `nextreel/web/routes/watched.py:70` (add route after `watched_list_page`)

- [ ] **Step 1: Add the import route**

Add after line 70 (after the `watched_list_page` function) in `nextreel/web/routes/watched.py`:

```python
@bp.route("/watched/import-letterboxd", methods=["POST"])
@csrf_required
async def import_letterboxd():
    redirect_response = _require_login()
    if redirect_response:
        return redirect_response

    from quart import flash, session as quart_session

    user_id = _current_user_id()
    services = _services()

    files = await request.files
    uploaded = files.get("letterboxd_csv")
    if not uploaded or not uploaded.filename:
        await flash("Please select a CSV file.", "error")
        return redirect(url_for("main.watched_list_page"))

    # Check file size (5MB limit)
    file_bytes = uploaded.stream.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        await flash("File is too large. Maximum size is 5MB.", "error")
        return redirect(url_for("main.watched_list_page"))

    from movies.letterboxd_import import match_films, parse_watched_csv

    import io

    try:
        films = parse_watched_csv(io.BytesIO(file_bytes))
    except ValueError as exc:
        await flash(
            f"Invalid CSV format: {exc}. Please upload the watched.csv from your Letterboxd export.",
            "error",
        )
        return redirect(url_for("main.watched_list_page"))

    if not films:
        await flash("The CSV file contained no films.", "warning")
        return redirect(url_for("main.watched_list_page"))

    try:
        result = await match_films(
            services.movie_manager.db_pool,
            films,
        )
        added = await services.movie_manager.watched_store.add_bulk(
            user_id, result.matched
        )
    except Exception:
        logger.exception("Letterboxd import failed for user %s", user_id)
        await flash("Something went wrong during import. Please try again.", "error")
        return redirect(url_for("main.watched_list_page"))

    # Build flash message
    matched_count = len(result.matched)
    unmatched_count = len(result.unmatched)
    if unmatched_count:
        await flash(
            f"Imported {matched_count} films. {unmatched_count} could not be matched.",
            "success",
        )
        quart_session["letterboxd_unmatched"] = [
            f"{u['name']} ({u['year']})" for u in result.unmatched[:50]
        ]
    else:
        await flash(f"Imported all {matched_count} films.", "success")

    logger.info(
        "Letterboxd import for user %s: %d matched, %d unmatched",
        user_id,
        matched_count,
        unmatched_count,
    )
    return redirect(url_for("main.watched_list_page"))
```

- [ ] **Step 2: Add necessary imports at the top of the file**

Add `url_for` to the existing quart import line in `nextreel/web/routes/watched.py:8`:

```python
from quart import abort, jsonify, redirect, render_template, request, url_for
```

- [ ] **Step 3: Add the new function to `__all__`**

Update `__all__` at the bottom of `nextreel/web/routes/watched.py`:

```python
__all__ = ["add_to_watched", "import_letterboxd", "remove_from_watched", "watched_list_page"]
```

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `python3 -m pytest tests/ -v --timeout=30 2>&1 | tail -20`
Expected: existing tests still pass

- [ ] **Step 5: Commit**

```bash
git add nextreel/web/routes/watched.py
git commit -m "feat: add POST /watched/import-letterboxd route"
```

---

### Task 5: Template — Import Section + Flash Messages

**Files:**
- Modify: `templates/watched_list.html`

- [ ] **Step 1: Add flash message rendering after the navbar**

Insert after `{% include 'navbar_modern.html' %}` (line 29) and before `<main id="main">` (line 31) in `templates/watched_list.html`:

```html
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
```

- [ ] **Step 2: Add import section to the watched header**

Insert after the `</dl>` closing tag (line 64) and before the `</header>` (line 65) in the `{% else %}` branch:

```html
        <details class="mt-4">
          <summary class="cursor-pointer text-sm font-medium text-gray-400 hover:text-gray-200 transition-colors">
            Import from Letterboxd
          </summary>
          <div class="mt-3 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
            <p class="text-xs text-gray-400 mb-3">
              Export your data from
              <a href="https://letterboxd.com/settings/data/" target="_blank" rel="noopener"
                 class="underline hover:text-gray-200">letterboxd.com/settings/data/</a>,
              then upload <code class="text-gray-300">watched.csv</code> below.
            </p>
            <form method="POST" action="{{ url_for('main.import_letterboxd') }}" enctype="multipart/form-data">
              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
              <div class="flex items-center gap-3">
                <input
                  type="file"
                  name="letterboxd_csv"
                  accept=".csv"
                  required
                  class="text-xs text-gray-300 file:mr-3 file:rounded-md file:border-0 file:bg-gray-700 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-gray-200 hover:file:bg-gray-600"
                />
                <button type="submit" class="rounded-md bg-blue-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-blue-500 transition-colors">
                  Import
                </button>
              </div>
            </form>
          </div>
        </details>
```

- [ ] **Step 3: Also add import section to the empty state**

Insert before `</div>` closing the `watched-empty` div (line 42), so users with no watched films can still import:

```html
        <details class="mt-6">
          <summary class="cursor-pointer text-sm font-medium text-gray-400 hover:text-gray-200 transition-colors">
            Import from Letterboxd
          </summary>
          <div class="mt-3 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
            <p class="text-xs text-gray-400 mb-3">
              Export your data from
              <a href="https://letterboxd.com/settings/data/" target="_blank" rel="noopener"
                 class="underline hover:text-gray-200">letterboxd.com/settings/data/</a>,
              then upload <code class="text-gray-300">watched.csv</code> below.
            </p>
            <form method="POST" action="{{ url_for('main.import_letterboxd') }}" enctype="multipart/form-data">
              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}" />
              <div class="flex items-center gap-3">
                <input
                  type="file"
                  name="letterboxd_csv"
                  accept=".csv"
                  required
                  class="text-xs text-gray-300 file:mr-3 file:rounded-md file:border-0 file:bg-gray-700 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-gray-200 hover:file:bg-gray-600"
                />
                <button type="submit" class="rounded-md bg-blue-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-blue-500 transition-colors">
                  Import
                </button>
              </div>
            </form>
          </div>
        </details>
```

- [ ] **Step 4: Commit**

```bash
git add templates/watched_list.html
git commit -m "feat: add Letterboxd import UI to watched list page"
```

---

### Task 6: Manual Integration Test

- [ ] **Step 1: Start the dev server**

```bash
python3 app.py
```

- [ ] **Step 2: Navigate to `/watched` in browser**

Verify:
- The "Import from Letterboxd" collapsible section appears
- Clicking it reveals the upload form with instructions
- The file input accepts only `.csv` files

- [ ] **Step 3: Upload the test CSV**

Upload `~/Downloads/letterboxd-billbadminton-2026-04-12-21-16-utc/watched.csv`.

Verify:
- Success flash message appears with matched/unmatched counts
- "Show unmatched films" expandable appears if there are unmatched films
- Watched list now shows the imported films
- Navigating away and back to `/watched` shows the films persisted

- [ ] **Step 4: Re-upload the same CSV**

Verify:
- Import succeeds again (idempotent)
- Count should be the same (no duplicates created)

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -v --timeout=30
```

Expected: all tests pass

- [ ] **Step 6: Commit any fixes from integration testing**

```bash
git add -A
git commit -m "fix: integration test fixes for Letterboxd import"
```
