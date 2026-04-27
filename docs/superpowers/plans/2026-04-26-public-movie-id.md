# Public Movie ID Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the IMDb identifier (`tt…`) in every public-facing URL with an internal opaque 6-char public ID, using Reddit-style URLs like `/movie/the-departed-2006-a8fk3j`.

**Architecture:** Add a `public_id CHAR(6)` column to `movie_projection` only. New `movies/public_id.py` module handles generation/resolution. New `movies/movie_url.py` builds Reddit-style paths from movie payloads. Routes get a top-of-handler "validate public_id → resolve to tconst → existing logic" translation. A Jinja global `movie_url(movie)` centralizes URL building for templates. Phased rollout: schema first, idempotent backfill at startup, then code cutover.

**Tech Stack:** Python 3.11+, Quart (async Flask), aiomysql via `SecureConnectionPool`, MySQL 8, pytest-asyncio (`asyncio_mode = "auto"`), Jinja2.

**Spec:** [docs/superpowers/specs/2026-04-26-public-movie-id-design.md](../specs/2026-04-26-public-movie-id-design.md)

---

## File map

**Created:**
- `movies/public_id.py` — ID generation, collision-safe assignment, resolver (tconst ↔ public_id).
- `movies/movie_url.py` — pure URL/slug builders (`title_slug`, `build_movie_path`, `parse_movie_path`).
- `tests/movies/test_public_id.py` — unit tests for the public_id module.
- `tests/movies/test_movie_url.py` — unit tests for URL/slug builders.
- `tests/web/test_public_id_routes.py` — integration-style route tests for the migrated routes.
- `tests/movies/test_public_id_backfill.py` — runtime-schema backfill tests.
- `tests/web/test_movie_dict_contract.py` — guard test that every code path producing a movie-shaped dict for templates includes `public_id` and `primaryTitle`.

**Modified:**
- `infra/runtime_schema.py` — adds the `public_id` column, unique index, backfill helper, and pre-deploy assertion helper.
- `movies/projection_repository.py` — `select_row` and `fetch_renderable_payloads` SELECT `public_id`; `payload_from_row` and `build_core_payload` carry it; `upsert_ready` and `ensure_core_projection` assign on insert.
- `movies/watched_store.py` — list query JOINs in `public_id`.
- `movies/watchlist_store.py` — list query JOINs in `public_id`.
- `movies/landing_film_service.py` — landing-film query SELECTs `public_id`.
- `nextreel/application/movie_navigator.py` — `_movie_ref` carries `public_id`; `candidate_store.fetch_ref`/`fetch_candidate_refs` include it.
- `movies/candidate_store.py` — `fetch_ref` and `fetch_candidate_refs` JOIN `movie_projection` to include `public_id` (when available).
- `nextreel/web/route_services.py` — `MovieDetailService.get` ensures `public_id` is present on the rendered movie dict.
- `nextreel/web/routes/shared.py` — adds `_resolve_public_id_or_404` helper; retires `_TCONST_RE` constant after callers switch.
- `nextreel/web/routes/movies.py` — `/movie/<slug_with_id>` handler with canonical-redirect.
- `nextreel/web/routes/watched.py` — POST handlers accept `<public_id>`.
- `nextreel/web/routes/watchlist.py` — POST handlers accept `<public_id>`.
- `nextreel/web/routes/search.py` — `/api/projection-state/<public_id>`.
- `nextreel/web/routes/navigation.py` — outbound redirects build `/movie/<slug_with_id>` via the URL helper.
- `nextreel/web/app.py` (or `nextreel/web/routes/shared.py:init_routes`) — registers `movie_url` Jinja global.
- `templates/movie_card.html` — switches to `movie_url(movie)` and uses `public_id` for POST forms.
- `templates/_watched_card.html`, `templates/_watchlist_card.html`, `templates/home.html` — switch to `movie_url(movie)`.
- `templates/movie.html` — `data-tconst` swapped to `data-public-id` (any JS that reads it updated to match).
- `static/js/movie-card.js`, `static/js/watchlist-toggle.js` — fetch URLs use the bare public_id.

---

## Task 1: Public ID module — pure generation primitive

**Files:**
- Create: `movies/public_id.py`
- Test: `tests/movies/test_public_id.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/movies/test_public_id.py`:

```python
"""Unit tests for movies.public_id."""

from __future__ import annotations

import re

import pytest

from movies.public_id import _ID_ALPHABET, _ID_LENGTH, _ID_RE, generate


class TestGenerate:
    def test_returns_six_chars(self):
        result = generate()
        assert len(result) == _ID_LENGTH == 6

    def test_uses_only_lowercase_alphanumeric(self):
        for _ in range(50):
            result = generate()
            assert all(ch in _ID_ALPHABET for ch in result)
            assert re.fullmatch(r"[a-z0-9]{6}", result)

    def test_varies_across_calls(self):
        # 50 generations should produce >40 distinct values (collisions
        # extremely improbable at 36^6 = 2.18B combos).
        results = {generate() for _ in range(50)}
        assert len(results) > 40


class TestIdRegex:
    def test_accepts_valid_id(self):
        assert _ID_RE.match("a8fk3j")
        assert _ID_RE.match("000000")
        assert _ID_RE.match("zzzzzz")

    def test_rejects_imdb_tconst(self):
        assert not _ID_RE.match("tt0393109")

    def test_rejects_uppercase(self):
        assert not _ID_RE.match("A8FK3J")
        assert not _ID_RE.match("a8FK3j")

    def test_rejects_wrong_length(self):
        assert not _ID_RE.match("a8fk3")     # 5 chars
        assert not _ID_RE.match("a8fk3jx")   # 7 chars
        assert not _ID_RE.match("")

    def test_rejects_special_chars(self):
        assert not _ID_RE.match("a8fk3-")
        assert not _ID_RE.match("a8 k3j")
        assert not _ID_RE.match("a8fk3!")
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `python3 -m pytest tests/movies/test_public_id.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'movies.public_id'`.

- [ ] **Step 1.3: Write the minimal implementation**

Create `movies/public_id.py`:

```python
"""Public movie identifier — opaque 6-char alphanumeric ID exposed in URLs.

The internal primary key (``tconst``) remains the IMDb identifier in storage.
This module owns the generation, validation, and resolution of the
public-facing alias used in URLs like ``/movie/the-departed-2006-a8fk3j``.
"""

from __future__ import annotations

import re
import secrets

_ID_LENGTH = 6
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 chars
_ID_RE = re.compile(r"^[a-z0-9]{6}$")
_MAX_GENERATION_ATTEMPTS = 8


class PublicIdGenerationError(Exception):
    """Raised when a unique public_id cannot be assigned after retries."""


def generate() -> str:
    """Return a fresh random public_id using a CSPRNG.

    No collision check — callers must handle the rare clash on insert.
    """
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


__all__ = [
    "PublicIdGenerationError",
    "_ID_ALPHABET",
    "_ID_LENGTH",
    "_ID_RE",
    "_MAX_GENERATION_ATTEMPTS",
    "generate",
]
```

- [ ] **Step 1.4: Run test to verify it passes**

Run: `python3 -m pytest tests/movies/test_public_id.py -v`
Expected: PASS — 7 tests pass.

- [ ] **Step 1.5: Commit**

```bash
git add movies/public_id.py tests/movies/test_public_id.py
git commit -m "feat(public_id): add generation primitive and validation regex"
```

---

## Task 2: Public ID module — DB-aware assignment with retry-on-collision

**Files:**
- Modify: `movies/public_id.py`
- Test: `tests/movies/test_public_id.py`

- [ ] **Step 2.1: Add tests for `assign_public_id`**

Append to `tests/movies/test_public_id.py`:

```python
from unittest.mock import AsyncMock

import pymysql
import pytest

from movies.public_id import (
    PublicIdGenerationError,
    _MAX_GENERATION_ATTEMPTS,
    assign_public_id,
)


class _FakePool:
    """Minimal mock matching the SecureConnectionPool.execute() shape used
    by the public_id module: ``await pool.execute(sql, params, fetch=...)``."""

    def __init__(self):
        self.execute = AsyncMock()


@pytest.fixture
def fake_pool():
    return _FakePool()


class TestAssignPublicId:
    async def test_returns_existing_id_without_writing(self, fake_pool):
        # First call fetches existing public_id.
        fake_pool.execute.return_value = {"public_id": "abcdef"}

        result = await assign_public_id(fake_pool, "tt0393109")

        assert result == "abcdef"
        # Only the SELECT, no UPDATE.
        assert fake_pool.execute.await_count == 1
        select_sql = fake_pool.execute.await_args_list[0][0][0]
        assert "SELECT public_id" in select_sql

    async def test_assigns_new_id_when_null(self, fake_pool):
        # SELECT returns row with NULL public_id, UPDATE returns 1 affected row.
        fake_pool.execute.side_effect = [
            {"public_id": None},  # SELECT
            1,                    # UPDATE affected row count
        ]

        result = await assign_public_id(fake_pool, "tt0393109")

        assert len(result) == 6
        assert all(ch in "abcdefghijklmnopqrstuvwxyz0123456789" for ch in result)
        update_sql = fake_pool.execute.await_args_list[1][0][0]
        assert "UPDATE movie_projection" in update_sql
        assert "public_id IS NULL" in update_sql

    async def test_returns_none_when_row_missing(self, fake_pool):
        # SELECT returns no row at all — caller's tconst doesn't exist.
        fake_pool.execute.return_value = None

        result = await assign_public_id(fake_pool, "tt9999999")

        assert result is None

    async def test_retries_on_duplicate_key_collision(self, fake_pool):
        dup_err = pymysql.err.IntegrityError(
            1062, "Duplicate entry 'aaaaaa' for key 'uq_movie_projection_public_id'"
        )
        # SELECT (NULL), UPDATE raises 1062 once, then UPDATE succeeds.
        fake_pool.execute.side_effect = [
            {"public_id": None},
            dup_err,
            1,
        ]

        result = await assign_public_id(fake_pool, "tt0393109")

        assert len(result) == 6
        # 1 SELECT + 2 UPDATE attempts.
        assert fake_pool.execute.await_count == 3

    async def test_raises_after_max_attempts(self, fake_pool):
        dup_err = pymysql.err.IntegrityError(
            1062, "Duplicate entry"
        )
        # 1 SELECT followed by N consecutive collisions.
        fake_pool.execute.side_effect = [{"public_id": None}] + [
            dup_err
        ] * _MAX_GENERATION_ATTEMPTS

        with pytest.raises(PublicIdGenerationError):
            await assign_public_id(fake_pool, "tt0393109")

    async def test_propagates_non_duplicate_errors(self, fake_pool):
        other_err = pymysql.err.OperationalError(2013, "connection lost")
        fake_pool.execute.side_effect = [
            {"public_id": None},
            other_err,
        ]

        with pytest.raises(pymysql.err.OperationalError):
            await assign_public_id(fake_pool, "tt0393109")
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_public_id.py -v -k assign_public_id`
Expected: FAIL with `ImportError: cannot import name 'assign_public_id' from 'movies.public_id'`.

- [ ] **Step 2.3: Implement `assign_public_id`**

Append to `movies/public_id.py`:

```python
import pymysql

from logging_config import get_logger

logger = get_logger(__name__)

_DUP_KEY_ERRNO = 1062


async def assign_public_id(pool, tconst: str) -> str | None:
    """Idempotently assign a public_id to a movie_projection row.

    Returns the assigned (or pre-existing) ID, or ``None`` if no row exists
    for ``tconst``. Safe under contention: the ``UPDATE ... WHERE public_id
    IS NULL`` clause guarantees only one writer wins, and a duplicate-key
    collision (1062) on the unique index triggers a retry with a fresh ID.
    """
    existing = await pool.execute(
        "SELECT public_id FROM movie_projection WHERE tconst = %s",
        [tconst],
        fetch="one",
    )
    if existing is None:
        return None
    current = existing.get("public_id") if isinstance(existing, dict) else existing[0]
    if current:
        return current

    last_error: Exception | None = None
    for _ in range(_MAX_GENERATION_ATTEMPTS):
        candidate = generate()
        try:
            affected = await pool.execute(
                """
                UPDATE movie_projection
                SET public_id = %s
                WHERE tconst = %s AND public_id IS NULL
                """,
                [candidate, tconst],
                fetch="none",
            )
        except pymysql.err.IntegrityError as exc:
            if exc.args and exc.args[0] == _DUP_KEY_ERRNO:
                last_error = exc
                continue
            raise
        if affected:
            return candidate
        # Affected = 0: another writer assigned in between our SELECT and
        # UPDATE. Re-read to return the winning value.
        re_read = await pool.execute(
            "SELECT public_id FROM movie_projection WHERE tconst = %s",
            [tconst],
            fetch="one",
        )
        if re_read:
            value = re_read.get("public_id") if isinstance(re_read, dict) else re_read[0]
            if value:
                return value
        # Row vanished mid-flight — treat as not-found.
        return None

    raise PublicIdGenerationError(
        f"Failed to assign public_id for {tconst} after "
        f"{_MAX_GENERATION_ATTEMPTS} attempts (last error: {last_error})"
    )
```

Update the module's `__all__`:

```python
__all__ = [
    "PublicIdGenerationError",
    "_ID_ALPHABET",
    "_ID_LENGTH",
    "_ID_RE",
    "_MAX_GENERATION_ATTEMPTS",
    "assign_public_id",
    "generate",
]
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_public_id.py -v`
Expected: PASS — all tests pass (originals from Task 1 + new ones).

- [ ] **Step 2.5: Commit**

```bash
git add movies/public_id.py tests/movies/test_public_id.py
git commit -m "feat(public_id): add idempotent assignment with collision retry"
```

---

## Task 3: Public ID module — resolvers (URL ↔ tconst lookups)

**Files:**
- Modify: `movies/public_id.py`
- Test: `tests/movies/test_public_id.py`

- [ ] **Step 3.1: Add resolver tests**

Append to `tests/movies/test_public_id.py`:

```python
from movies.public_id import public_id_for_tconst, resolve_to_tconst


class TestResolveToTconst:
    async def test_returns_none_for_invalid_format_without_db_hit(self, fake_pool):
        result = await resolve_to_tconst(fake_pool, "tt0393109")
        assert result is None
        # Format check short-circuits — no DB call.
        assert fake_pool.execute.await_count == 0

    async def test_returns_none_for_uppercase_input(self, fake_pool):
        assert await resolve_to_tconst(fake_pool, "A8FK3J") is None
        assert fake_pool.execute.await_count == 0

    async def test_returns_none_when_not_found(self, fake_pool):
        fake_pool.execute.return_value = None

        result = await resolve_to_tconst(fake_pool, "a8fk3j")

        assert result is None
        assert fake_pool.execute.await_count == 1

    async def test_returns_tconst_when_found(self, fake_pool):
        fake_pool.execute.return_value = {"tconst": "tt0393109"}

        result = await resolve_to_tconst(fake_pool, "a8fk3j")

        assert result == "tt0393109"
        sql = fake_pool.execute.await_args[0][0]
        assert "SELECT tconst FROM movie_projection" in sql
        assert "WHERE public_id = %s" in sql


class TestPublicIdForTconst:
    async def test_returns_id_when_present(self, fake_pool):
        fake_pool.execute.return_value = {"public_id": "a8fk3j"}

        result = await public_id_for_tconst(fake_pool, "tt0393109")

        assert result == "a8fk3j"

    async def test_returns_none_when_row_missing(self, fake_pool):
        fake_pool.execute.return_value = None

        result = await public_id_for_tconst(fake_pool, "tt9999999")

        assert result is None

    async def test_returns_none_when_public_id_null(self, fake_pool):
        fake_pool.execute.return_value = {"public_id": None}

        result = await public_id_for_tconst(fake_pool, "tt0393109")

        assert result is None
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_public_id.py::TestResolveToTconst -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3.3: Implement resolvers**

Append to `movies/public_id.py`:

```python
async def resolve_to_tconst(pool, public_id: str) -> str | None:
    """URL-side lookup: ``public_id`` (from path) → ``tconst`` (PK).

    Validates format before hitting the DB so malicious slugs short-circuit
    without a query. Returns ``None`` for both "invalid format" and "not
    found" so callers can map both to a single 404.
    """
    if not isinstance(public_id, str) or not _ID_RE.match(public_id):
        return None
    row = await pool.execute(
        "SELECT tconst FROM movie_projection WHERE public_id = %s LIMIT 1",
        [public_id],
        fetch="one",
    )
    if not row:
        return None
    return row.get("tconst") if isinstance(row, dict) else row[0]


async def public_id_for_tconst(pool, tconst: str) -> str | None:
    """Reverse lookup: ``tconst`` → ``public_id`` for outbound URL builders."""
    row = await pool.execute(
        "SELECT public_id FROM movie_projection WHERE tconst = %s LIMIT 1",
        [tconst],
        fetch="one",
    )
    if not row:
        return None
    value = row.get("public_id") if isinstance(row, dict) else row[0]
    return value or None
```

Update `__all__`:

```python
__all__ = [
    "PublicIdGenerationError",
    "_ID_ALPHABET",
    "_ID_LENGTH",
    "_ID_RE",
    "_MAX_GENERATION_ATTEMPTS",
    "assign_public_id",
    "generate",
    "public_id_for_tconst",
    "resolve_to_tconst",
]
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_public_id.py -v`
Expected: PASS — all tests in the file pass.

- [ ] **Step 3.5: Commit**

```bash
git add movies/public_id.py tests/movies/test_public_id.py
git commit -m "feat(public_id): add URL/tconst resolvers with format short-circuit"
```

---

## Task 4: URL/slug builder — pure helpers

**Files:**
- Create: `movies/movie_url.py`
- Test: `tests/movies/test_movie_url.py`

- [ ] **Step 4.1: Write failing tests**

Create `tests/movies/test_movie_url.py`:

```python
"""Unit tests for movies.movie_url — pure path/slug builders."""

from __future__ import annotations

import pytest

from movies.movie_url import (
    build_movie_path,
    parse_movie_path,
    title_slug,
)


class TestTitleSlug:
    def test_basic_title_with_year(self):
        assert title_slug("The Departed", 2006) == "the-departed-2006"

    def test_strips_diacritics(self):
        assert title_slug("Amélie", 2001) == "amelie-2001"
        assert title_slug("Pokémon", 1998) == "pokemon-1998"

    def test_collapses_special_characters(self):
        assert (
            title_slug("Star Wars: Episode IV — A New Hope", 1977)
            == "star-wars-episode-iv-a-new-hope-1977"
        )

    def test_keeps_digits_in_title(self):
        assert title_slug("3:10 to Yuma", 2007) == "3-10-to-yuma-2007"
        assert title_slug("2001: A Space Odyssey", 1968) == "2001-a-space-odyssey-1968"

    def test_empty_title_falls_back_to_untitled(self):
        assert title_slug("", 2006) == "untitled-2006"
        assert title_slug(None, 2006) == "untitled-2006"

    def test_year_omitted_when_missing(self):
        assert title_slug("The Departed", None) == "the-departed"
        assert title_slug("The Departed", "") == "the-departed"
        assert title_slug("The Departed", "Unknown") == "the-departed"

    def test_year_accepts_string_or_int(self):
        assert title_slug("X", 2006) == "x-2006"
        assert title_slug("X", "2006") == "x-2006"

    def test_truncates_long_titles_at_80_chars_no_trailing_hyphen(self):
        long_title = "A" * 200
        result = title_slug(long_title, 2006)
        # Body capped at 80 chars; year appended after.
        assert result == ("a" * 80) + "-2006"
        assert "--" not in result
        assert not result.endswith("-")

    def test_truncation_does_not_leave_trailing_hyphen(self):
        # Title where the 80-char cut would land mid-separator.
        title = ("ab" * 39) + " word"  # 78 chars + " word"
        result = title_slug(title, 2006)
        assert "--" not in result
        # The slug body before the year should not end with "-".
        body, year = result.rsplit("-", 1)
        assert not body.endswith("-")


class TestBuildMoviePath:
    def test_renders_canonical_path(self):
        assert (
            build_movie_path("The Departed", 2006, "a8fk3j")
            == "/movie/the-departed-2006-a8fk3j"
        )

    def test_handles_missing_year(self):
        assert build_movie_path("The Departed", None, "a8fk3j") == "/movie/the-departed-a8fk3j"


class TestParseMoviePath:
    def test_parses_canonical(self):
        assert parse_movie_path("the-departed-2006-a8fk3j") == (
            "the-departed-2006",
            "a8fk3j",
        )

    def test_parses_minimal_one_char_title(self):
        # "M-1931-aaaaaa" — slug body is "m-1931", id is "aaaaaa".
        assert parse_movie_path("m-1931-aaaaaa") == ("m-1931", "aaaaaa")

    def test_returns_none_for_id_only(self):
        # No leading title — must have at least one slug char before the ID.
        assert parse_movie_path("a8fk3j") is None

    def test_returns_none_for_garbage(self):
        assert parse_movie_path("nonsense") is None
        assert parse_movie_path("") is None
        assert parse_movie_path("a/b") is None
        assert parse_movie_path("ABC-a8fk3j") is None  # uppercase rejected
        assert parse_movie_path("x-A8FK3J") is None    # uppercase ID rejected

    def test_returns_none_when_id_segment_wrong_length(self):
        assert parse_movie_path("title-12345") is None  # only 5 chars
        assert parse_movie_path("title-1234567") is None  # 7 chars
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_movie_url.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'movies.movie_url'`.

- [ ] **Step 4.3: Implement the module**

Create `movies/movie_url.py`:

```python
"""Pure helpers for building Reddit-style movie URLs.

URL shape: ``/movie/<title-slug>-<6-char-public-id>``. The trailing 6 chars
are the canonical key the route resolves on; the title slug is decorative
and can be regenerated when titles are corrected (a slug-mismatch on
request triggers a 301 to the canonical form, handled in the route).
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_TRAILING_HYPHENS_RE = re.compile(r"-+$")
_LEADING_HYPHENS_RE = re.compile(r"^-+")
_YEAR_RE = re.compile(r"^\d{4}$")
_TITLE_BODY_MAX_CHARS = 80
_PATH_RE = re.compile(r"^(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<public_id>[a-z0-9]{6})$")


def _slugify_body(title: str | None) -> str:
    if not title:
        return "untitled"
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    lowered = folded.lower()
    collapsed = _NON_ALNUM_RE.sub("-", lowered)
    trimmed = _LEADING_HYPHENS_RE.sub("", collapsed)
    trimmed = _TRAILING_HYPHENS_RE.sub("", trimmed)
    if not trimmed:
        return "untitled"
    if len(trimmed) > _TITLE_BODY_MAX_CHARS:
        trimmed = trimmed[:_TITLE_BODY_MAX_CHARS]
        trimmed = _TRAILING_HYPHENS_RE.sub("", trimmed)
    return trimmed or "untitled"


def _coerce_year(year) -> str | None:
    if year is None:
        return None
    candidate = str(year).strip()
    if _YEAR_RE.match(candidate):
        return candidate
    return None


def title_slug(primary_title: str | None, year) -> str:
    """Return ``the-departed-2006``-style slug for a movie.

    Strips diacritics, lowercases, replaces non-alphanumeric runs with a
    single hyphen, trims hyphens, caps the title body at 80 chars, and
    appends ``-<year>`` when ``year`` is parseable as a 4-digit number.
    """
    body = _slugify_body(primary_title)
    year_str = _coerce_year(year)
    if year_str:
        return f"{body}-{year_str}"
    return body


def build_movie_path(primary_title: str | None, year, public_id: str) -> str:
    """Return the canonical ``/movie/...`` path for a movie."""
    return f"/movie/{title_slug(primary_title, year)}-{public_id}"


def parse_movie_path(slug_with_id: str) -> tuple[str, str] | None:
    """Parse a movie URL slug into ``(slug_prefix, public_id)``.

    Returns ``None`` when the input is malformed (wrong shape, uppercase,
    bad ID length). Used by the ``/movie/<slug_with_id>`` route handler to
    extract the public_id and decide whether to 301 to canonical.
    """
    if not isinstance(slug_with_id, str) or not slug_with_id:
        return None
    match = _PATH_RE.match(slug_with_id)
    if not match:
        return None
    return match.group("slug"), match.group("public_id")


__all__ = ["build_movie_path", "parse_movie_path", "title_slug"]
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_movie_url.py -v`
Expected: PASS — all tests pass.

- [ ] **Step 4.5: Commit**

```bash
git add movies/movie_url.py tests/movies/test_movie_url.py
git commit -m "feat(movie_url): add pure path/slug builders for public ID URLs"
```

---

## Task 5: Schema — add `public_id` column and unique index

**Files:**
- Modify: `infra/runtime_schema.py`
- Test: `tests/infra/test_runtime_schema.py`

- [ ] **Step 5.1: Add tests for the new helper and the orchestrator wiring**

Append to `tests/infra/test_runtime_schema.py`:

```python
from infra.runtime_schema import ensure_movie_projection_public_id_column


async def test_ensure_movie_projection_public_id_column_creates_when_missing(mock_db_pool):
    await ensure_movie_projection_public_id_column(mock_db_pool)

    # Two DDLs: ADD COLUMN, then ADD UNIQUE INDEX.
    assert mock_db_pool._ddl_cursor.execute.await_count == 2
    sqls = [call.args[0] for call in mock_db_pool._ddl_cursor.execute.await_args_list]
    assert any(
        "ADD COLUMN public_id CHAR(6) NULL" in sql for sql in sqls
    )
    assert any(
        "ADD UNIQUE INDEX uq_movie_projection_public_id" in sql for sql in sqls
    )


async def test_ensure_movie_projection_public_id_column_swallows_dup_column(mock_db_pool):
    """Errno 1060 (duplicate column) is treated as 'already exists'."""
    mock_db_pool._ddl_cursor.execute.side_effect = [
        pymysql.err.OperationalError(1060, "Duplicate column name 'public_id'"),
        None,  # the unique index call still runs
    ]

    await ensure_movie_projection_public_id_column(mock_db_pool)

    assert mock_db_pool._ddl_cursor.execute.await_count == 2
```

Also update `_RUNTIME_SCHEMA_REPAIR_HELPERS` in the same test file (it asserts the orchestrator helper list matches what's registered):

```python
_RUNTIME_SCHEMA_REPAIR_HELPERS = [
    "ensure_user_navigation_current_ref_column",
    "ensure_movie_candidates_shuffle_key",
    "ensure_movie_candidates_refreshed_at_index",
    "ensure_movie_candidates_shuffle_key_index",
    "ensure_movie_candidates_bucket_filter_index",
    "ensure_movie_candidates_primaryTitle_index",
    "ensure_movie_candidates_fulltext_index",
    "ensure_movie_projection_state_last_attempt_index",
    "ensure_user_navigation_user_id_column",
    "ensure_users_exclude_watched_default_column",
    "ensure_users_theme_preference_column",
    "ensure_users_default_filters_json_column",
    "ensure_users_exclude_watchlist_default_column",
    "ensure_movie_projection_public_id_column",
]
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py::test_ensure_movie_projection_public_id_column_creates_when_missing -v`
Expected: FAIL with `ImportError: cannot import name 'ensure_movie_projection_public_id_column'`.

- [ ] **Step 5.3: Implement the helper and register it**

In `infra/runtime_schema.py`, add a new helper near the existing
projection-related helpers (e.g. right after
`ensure_movie_projection_state_last_attempt_index`):

```python
async def ensure_movie_projection_public_id_column(db_pool) -> None:
    """Add the additive public_id column and its unique index.

    The column starts NULLable to permit a startup-time backfill of existing
    rows. A separate helper (``ensure_movie_projection_public_id_backfill``)
    populates NULLs and then tightens the column to NOT NULL.
    """
    await _ensure_column(
        db_pool,
        "movie_projection",
        "public_id",
        """
        ALTER TABLE movie_projection
        ADD COLUMN public_id CHAR(6) NULL
        """,
    )
    await _ensure_index(
        db_pool,
        "movie_projection",
        "uq_movie_projection_public_id",
        """
        ALTER TABLE movie_projection
        ADD UNIQUE INDEX uq_movie_projection_public_id (public_id)
        """,
    )
```

Append the helper name to `_RUNTIME_REPAIR_HELPER_NAMES` (at the end of
the tuple — the repair helpers run in declaration order):

```python
_RUNTIME_REPAIR_HELPER_NAMES = (
    "ensure_user_navigation_current_ref_column",
    "ensure_movie_candidates_shuffle_key",
    "ensure_movie_candidates_refreshed_at_index",
    "ensure_movie_candidates_shuffle_key_index",
    "ensure_movie_candidates_bucket_filter_index",
    "ensure_movie_candidates_primaryTitle_index",
    "ensure_movie_candidates_fulltext_index",
    "ensure_movie_projection_state_last_attempt_index",
    "ensure_user_navigation_user_id_column",
    "ensure_users_exclude_watched_default_column",
    "ensure_users_theme_preference_column",
    "ensure_users_default_filters_json_column",
    "ensure_users_exclude_watchlist_default_column",
    "ensure_movie_projection_public_id_column",
)
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `python3 -m pytest tests/infra/test_runtime_schema.py -v`
Expected: PASS — new tests pass, existing tests still pass (the repair-helper-list assertion now matches).

- [ ] **Step 5.5: Commit**

```bash
git add infra/runtime_schema.py tests/infra/test_runtime_schema.py
git commit -m "feat(schema): add movie_projection.public_id column and unique index"
```

---

## Task 6: Schema — backfill NULL public_ids and tighten to NOT NULL

**Files:**
- Modify: `infra/runtime_schema.py`
- Modify: `tests/infra/test_runtime_schema.py`
- Create: `tests/movies/test_public_id_backfill.py`

- [ ] **Step 6.1: Write failing tests for the backfill helper**

Create `tests/movies/test_public_id_backfill.py`:

```python
"""Tests for the public_id backfill helper."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from infra.runtime_schema import ensure_movie_projection_public_id_backfill


class _ScriptedPool:
    """Records every execute() call in order, replaying scripted return values.

    Mirrors the ``await pool.execute(sql, params, fetch=...)`` shape used by
    the runtime_schema helpers.
    """

    def __init__(self, scripted: list):
        self._scripted = list(scripted)
        self.calls: list[tuple[str, list, str | None]] = []
        self.execute = AsyncMock(side_effect=self._next_response)

    async def _next_response(self, sql, params=None, fetch=None):
        self.calls.append((sql, params, fetch))
        if not self._scripted:
            raise AssertionError(f"Unscripted execute({sql!r})")
        return self._scripted.pop(0)


async def test_short_circuits_when_already_done():
    """If the runtime_metadata flag is set, the helper does no work."""
    pool = _ScriptedPool(
        scripted=[
            {"meta_value": "1"},  # _get_runtime_flag returns truthy
        ]
    )

    await ensure_movie_projection_public_id_backfill(pool)

    assert pool.execute.await_count == 1


async def test_backfills_null_rows_then_tightens_column():
    pool = _ScriptedPool(
        scripted=[
            None,  # _get_runtime_flag → not done
            [{"tconst": "tt0000001"}, {"tconst": "tt0000002"}],  # SELECT NULL rows
            1,     # UPDATE row 1
            1,     # UPDATE row 2
            None,  # ALTER MODIFY COLUMN
            None,  # _set_runtime_flag insert
        ]
    )

    await ensure_movie_projection_public_id_backfill(pool)

    # Sanity-check the high-level shape of the SQL sequence:
    sqls = [call[0] for call in pool.calls]
    assert "FROM runtime_metadata" in sqls[0]
    assert "FROM movie_projection" in sqls[1] and "public_id IS NULL" in sqls[1]
    assert "UPDATE movie_projection" in sqls[2]
    assert "MODIFY COLUMN public_id CHAR(6) NOT NULL" in sqls[4]
    assert "INSERT INTO runtime_metadata" in sqls[5]


async def test_no_null_rows_still_tightens_and_records_flag():
    pool = _ScriptedPool(
        scripted=[
            None,  # not done
            [],    # SELECT returns no NULL rows
            None,  # ALTER MODIFY COLUMN
            None,  # _set_runtime_flag
        ]
    )

    await ensure_movie_projection_public_id_backfill(pool)

    assert pool.execute.await_count == 4
    assert "MODIFY COLUMN public_id CHAR(6) NOT NULL" in pool.calls[2][0]
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_public_id_backfill.py -v`
Expected: FAIL with `ImportError: cannot import name 'ensure_movie_projection_public_id_backfill'`.

- [ ] **Step 6.3: Implement the backfill helper**

In `infra/runtime_schema.py`, add the helper (place it after
`ensure_movie_projection_public_id_column`):

```python
async def ensure_movie_projection_public_id_backfill(db_pool) -> None:
    """Populate any NULL public_id rows, then tighten the column to NOT NULL.

    Idempotent and crash-safe: gated by the
    ``public_id_backfill_done`` flag in ``runtime_metadata``. If the process
    is interrupted mid-loop, the next startup picks up where it left off
    because the SELECT only sees rows still NULL.

    Concurrent enrichment writes are tolerated: ``assign_public_id`` issues
    ``UPDATE ... WHERE public_id IS NULL``, so the first writer wins and
    later writers no-op.
    """
    if await _get_runtime_flag(db_pool, "public_id_backfill_done"):
        logger.debug("public_id backfill already complete, skipping")
        return

    # Lazy import — public_id depends on logging_config but not the schema
    # module, so importing here keeps module load order safe.
    from movies.public_id import assign_public_id

    rows = await db_pool.execute(
        """
        SELECT tconst FROM movie_projection
        WHERE public_id IS NULL
        ORDER BY tconst
        """,
        fetch="all",
    )
    rows = rows or []
    total_updated = 0
    for row in rows:
        tconst = row["tconst"] if isinstance(row, dict) else row[0]
        result = await assign_public_id(db_pool, tconst)
        if result:
            total_updated += 1

    await db_pool.execute(
        """
        ALTER TABLE movie_projection
        MODIFY COLUMN public_id CHAR(6) NOT NULL
        """,
        fetch="none",
    )
    await _set_runtime_flag(db_pool, "public_id_backfill_done", "1")
    logger.info("public_id backfill complete (%d rows updated)", total_updated)
```

Register the helper at the **end** of `_RUNTIME_REPAIR_HELPER_NAMES` (after
`ensure_movie_projection_public_id_column` so the column exists first):

```python
_RUNTIME_REPAIR_HELPER_NAMES = (
    "ensure_user_navigation_current_ref_column",
    "ensure_movie_candidates_shuffle_key",
    "ensure_movie_candidates_refreshed_at_index",
    "ensure_movie_candidates_shuffle_key_index",
    "ensure_movie_candidates_bucket_filter_index",
    "ensure_movie_candidates_primaryTitle_index",
    "ensure_movie_candidates_fulltext_index",
    "ensure_movie_projection_state_last_attempt_index",
    "ensure_user_navigation_user_id_column",
    "ensure_users_exclude_watched_default_column",
    "ensure_users_theme_preference_column",
    "ensure_users_default_filters_json_column",
    "ensure_users_exclude_watchlist_default_column",
    "ensure_movie_projection_public_id_column",
    "ensure_movie_projection_public_id_backfill",
)
```

Also append the helper to the `_RUNTIME_SCHEMA_REPAIR_HELPERS` list in
`tests/infra/test_runtime_schema.py` so the orchestrator-list assertion
keeps passing.

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_public_id_backfill.py tests/infra/test_runtime_schema.py -v`
Expected: PASS — all tests pass.

- [ ] **Step 6.5: Commit**

```bash
git add infra/runtime_schema.py tests/infra/test_runtime_schema.py tests/movies/test_public_id_backfill.py
git commit -m "feat(schema): backfill movie_projection.public_id and tighten to NOT NULL"
```

---

## Task 7: Repository writes — assign public_id during enrichment

**Files:**
- Modify: `movies/projection_repository.py`
- Test: `tests/movies/test_projection_repository.py`

- [ ] **Step 7.1: Write failing test that asserts assign_public_id is called after upsert_ready**

Append to `tests/movies/test_projection_repository.py`:

```python
from unittest.mock import AsyncMock, patch

from movies.projection_repository import ProjectionRepository
from movies.projection_state import ProjectionState


async def test_upsert_ready_assigns_public_id():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    repo = ProjectionRepository(pool)

    payload = {
        "tconst": "tt0393109",
        "tmdb_id": 1234,
        "title": "The Departed",
        "year": "2006",
        "_full": True,
    }

    with patch("movies.projection_repository.assign_public_id", new=AsyncMock(return_value="a8fk3j")) as assigner:
        await repo.upsert_ready("tt0393109", payload, now=__import__("datetime").datetime(2026, 4, 26, 12, 0, 0), attempts=1)

    assigner.assert_awaited_once_with(pool, "tt0393109")


async def test_ensure_core_projection_assigns_public_id():
    pool = AsyncMock()
    # First execute is the title.basics SELECT; return a row.
    pool.execute = AsyncMock(side_effect=[
        {
            "tconst": "tt0393109",
            "primaryTitle": "The Departed",
            "startYear": 2006,
            "genres": "Crime,Drama",
            "language": "en",
            "slug": "the-departed-2006",
            "averageRating": 8.5,
            "numVotes": 100000,
        },
        None,  # INSERT ... ON DUPLICATE KEY UPDATE
    ])
    repo = ProjectionRepository(pool)

    with patch("movies.projection_repository.assign_public_id", new=AsyncMock(return_value="a8fk3j")) as assigner:
        result = await repo.ensure_core_projection("tt0393109")

    assert result is not None
    assigner.assert_awaited_once_with(pool, "tt0393109")
```

- [ ] **Step 7.2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_projection_repository.py::test_upsert_ready_assigns_public_id tests/movies/test_projection_repository.py::test_ensure_core_projection_assigns_public_id -v`
Expected: FAIL with `AttributeError: ... has no attribute 'assign_public_id'` (the import doesn't exist yet).

- [ ] **Step 7.3: Wire `assign_public_id` into the upsert paths**

In `movies/projection_repository.py`, add an import near the top:

```python
from movies.public_id import assign_public_id
```

In `upsert_ready`, append after the existing `await self.db_pool.execute(...)` block:

```python
        try:
            await assign_public_id(self.db_pool, tconst)
        except Exception:  # noqa: BLE001 — best-effort
            # Backfill / next enrichment will pick this up. A failure here
            # must not roll back the projection write.
            from logging_config import get_logger
            get_logger(__name__).warning(
                "public_id assignment failed for %s; will retry on next enrichment",
                tconst,
                exc_info=True,
            )
```

In `ensure_core_projection`, append the same try/except after the existing
`INSERT ... ON DUPLICATE KEY UPDATE` execute call (right before
`return payload`).

- [ ] **Step 7.4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_projection_repository.py -v`
Expected: PASS — new tests pass, existing tests still pass.

- [ ] **Step 7.5: Commit**

```bash
git add movies/projection_repository.py tests/movies/test_projection_repository.py
git commit -m "feat(projection): assign public_id on upsert_ready and ensure_core"
```

---

## Task 8: Repository reads — surface `public_id` in projection payloads

**Files:**
- Modify: `movies/projection_repository.py`
- Test: `tests/movies/test_projection_repository.py`

- [ ] **Step 8.1: Write failing tests**

Append to `tests/movies/test_projection_repository.py`:

```python
async def test_select_row_returns_public_id():
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value={
        "tconst": "tt0393109",
        "tmdb_id": 1234,
        "payload_json": '{"title": "The Departed"}',
        "projection_state": "ready",
        "enriched_at": None,
        "stale_after": None,
        "last_attempt_at": None,
        "attempt_count": 0,
        "last_error": None,
        "public_id": "a8fk3j",
    })
    repo = ProjectionRepository(pool)

    row = await repo.select_row("tt0393109")

    sql = pool.execute.await_args[0][0]
    assert "public_id" in sql
    assert row["public_id"] == "a8fk3j"


def test_payload_from_row_carries_public_id():
    repo = ProjectionRepository(db_pool=None)
    payload = repo.payload_from_row({
        "tconst": "tt0393109",
        "payload_json": '{"title": "The Departed"}',
        "projection_state": "ready",
        "public_id": "a8fk3j",
    })
    assert payload["public_id"] == "a8fk3j"


def test_payload_from_row_omits_public_id_when_missing():
    repo = ProjectionRepository(db_pool=None)
    payload = repo.payload_from_row({
        "tconst": "tt0393109",
        "payload_json": '{"title": "The Departed"}',
        "projection_state": "ready",
    })
    # Absence is preserved as None so callers can distinguish "loaded
    # before backfill" from "explicitly empty".
    assert payload.get("public_id") is None


def test_build_core_payload_includes_public_id_field():
    repo = ProjectionRepository(db_pool=None)
    payload = repo.build_core_payload({
        "tconst": "tt0393109",
        "primaryTitle": "The Departed",
        "startYear": 2006,
        "genres": "Crime,Drama",
        "language": "en",
        "slug": "the-departed-2006",
        "averageRating": 8.5,
        "numVotes": 100000,
    })
    # Always present, even at CORE state — populated by the post-insert assign.
    assert "public_id" in payload
    assert payload["public_id"] is None  # not yet assigned at this point
```

- [ ] **Step 8.2: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_projection_repository.py -v -k "public_id"`
Expected: FAIL — `select_row` SQL doesn't yet include `public_id`, and the payloads don't carry the field.

- [ ] **Step 8.3: Update repository SQL and payload builders**

In `movies/projection_repository.py`:

1. Update both `select_row` and `fetch_renderable_payloads` SELECT lists to add `public_id`:

```python
    async def select_row(self, tconst: str) -> dict[str, Any] | None:
        return await self.db_pool.execute(
            """
            SELECT tconst, tmdb_id, payload_json, projection_state,
                   enriched_at, stale_after, last_attempt_at, attempt_count, last_error,
                   public_id
            FROM movie_projection
            WHERE tconst = %s
            """,
            [tconst],
            fetch="one",
        )
```

```python
        sql = f"""
            SELECT tconst, tmdb_id, payload_json, projection_state,
                   enriched_at, stale_after, last_attempt_at, attempt_count, last_error,
                   public_id
            FROM movie_projection
            WHERE tconst IN ({placeholders})
        """
```

2. Update `payload_from_row` to copy `public_id` from the row into the payload:

```python
    def payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload_json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("projection_state", row.get("projection_state"))
        payload.setdefault("tconst", row.get("tconst"))
        # Public ID is sourced from the row column, not payload_json — it's
        # canonical metadata, not part of the rendered movie body.
        payload.setdefault("public_id", row.get("public_id"))
        return payload
```

3. Update `build_core_payload` to include the field (always present in the
   dict, value `None` until the post-insert assign populates it):

```python
        return {
            "title": row.get("primaryTitle") or "Unknown",
            "tconst": row["tconst"],
            "imdb_id": row["tconst"],
            "tmdb_id": None,
            "public_id": None,
            "slug": row.get("slug"),
            ...
```

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_projection_repository.py -v`
Expected: PASS.

- [ ] **Step 8.5: Commit**

```bash
git add movies/projection_repository.py tests/movies/test_projection_repository.py
git commit -m "feat(projection): surface public_id in select_row and payloads"
```

---

## Task 9: Watched/watchlist list queries — JOIN public_id

**Files:**
- Modify: `movies/watched_store.py`
- Modify: `movies/watchlist_store.py`
- Test: `tests/movies/test_watched_store.py` (or wherever list-query tests live)
- Test: `tests/movies/test_watchlist_store.py`

- [ ] **Step 9.1: Inspect existing list query tests to mirror style**

Run: `python3 -m pytest tests/movies/test_watched_store.py -v --collect-only 2>&1 | head -40` (if the file exists).

If no test file exists for the list queries, locate where list-query tests
live (look for `list_watched_filtered` or `list_watchlist_filtered` in
`tests/movies/` and `tests/web/`).

- [ ] **Step 9.2: Write failing tests asserting `public_id` is selected**

Add a test (in the file you found in 9.1, or `tests/movies/test_watchlist_store.py` if absent):

```python
async def test_list_watchlist_filtered_selects_public_id():
    """The list query must include public_id so templates can build URLs."""
    from movies.watchlist_store import WatchlistStore
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=[])

    store = WatchlistStore(pool)
    await store.list_watchlist_filtered("user-123")

    sql = pool.execute.await_args[0][0]
    assert "p.public_id" in sql
```

Same pattern for the watched store.

- [ ] **Step 9.3: Run tests to verify they fail**

Run: `python3 -m pytest tests/movies/test_watchlist_store.py -v -k public_id`
Expected: FAIL — current SELECT list does not include `p.public_id`.

- [ ] **Step 9.4: Update `movies/watchlist_store.py`'s `list_watchlist_filtered`**

The current outer SELECT is:

```sql
SELECT sub.tconst, sub.added_at,
       sub.primaryTitle, sub.startYear, sub.genres, sub.slug,
       sub.averageRating,
       p.payload_json
FROM ( ... ) sub
LEFT JOIN movie_projection p ON sub.tconst = p.tconst
```

Change the outer SELECT to also project `p.public_id`:

```sql
SELECT sub.tconst, sub.added_at,
       sub.primaryTitle, sub.startYear, sub.genres, sub.slug,
       sub.averageRating,
       p.payload_json, p.public_id
FROM ( ... ) sub
LEFT JOIN movie_projection p ON sub.tconst = p.tconst
```

- [ ] **Step 9.5: Repeat for `movies/watched_store.py`**

Apply the same change in every place `watched_store.py` joins
`movie_projection p` to surface `payload_json`. Per the earlier grep,
those are at lines 167, 192, 279, and 439. Add `, p.public_id` to each
SELECT list that already includes `p.payload_json`.

- [ ] **Step 9.6: Run tests to verify they pass**

Run: `python3 -m pytest tests/movies/test_watched_store.py tests/movies/test_watchlist_store.py -v`
Expected: PASS.

- [ ] **Step 9.7: Commit**

```bash
git add movies/watched_store.py movies/watchlist_store.py tests/movies/test_watched_store.py tests/movies/test_watchlist_store.py
git commit -m "feat(stores): surface public_id from movie_projection JOIN in list queries"
```

---

## Task 10: Landing-film service — surface public_id

**Files:**
- Modify: `movies/landing_film_service.py`
- Test: `tests/movies/test_landing_film_service.py`

- [ ] **Step 10.1: Inspect the existing query**

Run: `grep -n "SELECT\|fetch_random_landing_film" movies/landing_film_service.py | head -20`

- [ ] **Step 10.2: Write a failing test**

Append to `tests/movies/test_landing_film_service.py`:

```python
async def test_landing_film_query_selects_public_id():
    from movies.landing_film_service import fetch_random_landing_film
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)

    await fetch_random_landing_film(pool)

    sql = pool.execute.await_args[0][0]
    assert "public_id" in sql
```

- [ ] **Step 10.3: Run test to verify failure**

Run: `python3 -m pytest tests/movies/test_landing_film_service.py -v -k public_id`
Expected: FAIL.

- [ ] **Step 10.4: Add `public_id` to the SELECT**

Edit the SELECT list in the landing-film SQL to include `public_id`
alongside the other projected columns (e.g. add `, public_id` after the
existing column list).

- [ ] **Step 10.5: Run test to verify pass**

Run: `python3 -m pytest tests/movies/test_landing_film_service.py -v`
Expected: PASS.

- [ ] **Step 10.6: Commit**

```bash
git add movies/landing_film_service.py tests/movies/test_landing_film_service.py
git commit -m "feat(landing): include public_id in landing-film SELECT"
```

---

## Task 11: Movie detail view-model — guarantee `public_id` on rendered dict

**Files:**
- Modify: `nextreel/web/route_services.py`
- Test: `tests/web/test_route_services.py`

- [ ] **Step 11.1: Write a failing test**

Append to `tests/web/test_route_services.py`:

```python
async def test_movie_detail_view_model_carries_public_id():
    from nextreel.web.route_services import MovieDetailService

    payload = {
        "tconst": "tt0393109",
        "title": "The Departed",
        "public_id": "a8fk3j",
        "_full": True,
    }
    movie_manager = SimpleNamespace(
        projection_store=SimpleNamespace(
            fetch_renderable_payload=AsyncMock(return_value=payload)
        ),
        watched_store=SimpleNamespace(is_watched=AsyncMock(return_value=False)),
        watchlist_store=SimpleNamespace(is_in_watchlist=AsyncMock(return_value=False)),
        prev_stack_length=lambda state: 0,
    )

    service = MovieDetailService()
    view_model = await service.get(
        movie_manager=movie_manager,
        state=SimpleNamespace(),
        user_id=None,
        tconst="tt0393109",
    )

    assert view_model is not None
    assert view_model.movie["public_id"] == "a8fk3j"
```

(Use `from types import SimpleNamespace` and `from unittest.mock import AsyncMock` if not already imported in the file.)

- [ ] **Step 11.2: Run test to verify it fails**

Run: `python3 -m pytest tests/web/test_route_services.py::test_movie_detail_view_model_carries_public_id -v`
Expected: PASS already if `payload_from_row` from Task 8 propagates correctly. If it fails, investigate why.

If it passes immediately because of Task 8's groundwork: skip directly to
the commit step. If it fails, the issue is most likely that
`fetch_renderable_payload` strips fields not in a hard-coded allowlist —
check that path and ensure `public_id` is preserved.

- [ ] **Step 11.3: Make any needed fix**

If `nextreel/web/route_services.py` `MovieDetailService.get` builds a
projection of the payload, ensure the projection includes `public_id`. If
the payload is passed through verbatim, no change is needed.

- [ ] **Step 11.4: Run test to verify pass**

Run: `python3 -m pytest tests/web/test_route_services.py -v`
Expected: PASS.

- [ ] **Step 11.5: Commit**

```bash
git add nextreel/web/route_services.py tests/web/test_route_services.py
git commit -m "test(route_services): assert movie detail view model carries public_id"
```

---

## Task 12: Navigator — carry public_id in stack entries

**Files:**
- Modify: `nextreel/application/movie_navigator.py`
- Modify: `movies/candidate_store.py` (only if `fetch_candidate_refs` controls the dict shape)
- Test: `tests/application/test_movie_navigator.py`

- [ ] **Step 12.1: Inspect candidate_store.fetch_ref / fetch_candidate_refs**

Run: `grep -n "def fetch_ref\|def fetch_candidate_refs\|public_id" movies/candidate_store.py | head -20`

If `fetch_ref` and `fetch_candidate_refs` query `movie_candidates` (the
IMDb-derived table that does *not* have `public_id`), they need to LEFT
JOIN `movie_projection` to surface it. If they already join projection,
just add `p.public_id` to the SELECT.

- [ ] **Step 12.2: Write a failing test in `tests/application/test_movie_navigator.py`**

Add (or create the file if missing):

```python
from unittest.mock import AsyncMock

from nextreel.application.movie_navigator import _movie_ref


def test_movie_ref_includes_public_id_when_provided():
    ref = _movie_ref({
        "tconst": "tt0393109",
        "imdb_id": "tt0393109",
        "title": "The Departed",
        "slug": "the-departed-2006",
        "public_id": "a8fk3j",
    })
    assert ref["public_id"] == "a8fk3j"


def test_movie_ref_falls_back_to_none_when_missing():
    ref = _movie_ref({
        "tconst": "tt0393109",
        "title": "The Departed",
        "slug": "the-departed-2006",
    })
    assert ref.get("public_id") is None
```

- [ ] **Step 12.3: Run tests to verify they fail**

Run: `python3 -m pytest tests/application/test_movie_navigator.py -v -k movie_ref`
Expected: FAIL — current `_movie_ref` doesn't include `public_id`.

- [ ] **Step 12.4: Update `_movie_ref` to include public_id**

In `nextreel/application/movie_navigator.py`:

```python
def _movie_ref(movie_data: dict) -> dict:
    """Extract the lightweight reference stored in navigation state."""
    return {
        "tconst": movie_data.get("tconst") or movie_data.get("imdb_id"),
        "title": movie_data.get("title"),
        "slug": movie_data.get("slug"),
        "public_id": movie_data.get("public_id"),
    }
```

- [ ] **Step 12.5: Update `movies/candidate_store.py` to surface public_id**

Find `fetch_ref` and `fetch_candidate_refs`. The fixes depend on whether
the existing queries already join `movie_projection`:

- If they query `movie_candidates` only: add a `LEFT JOIN movie_projection
  p ON p.tconst = c.tconst` and project `p.public_id` in the SELECT.
- If they already join projection: append `, p.public_id` to the SELECT.

The dict the function returns should now include the key `public_id`
(value `None` when no projection row exists yet).

- [ ] **Step 12.6: Run tests to verify they pass**

Run: `python3 -m pytest tests/application/test_movie_navigator.py tests/movies/test_candidate_store.py -v`
Expected: PASS.

- [ ] **Step 12.7: Commit**

```bash
git add nextreel/application/movie_navigator.py movies/candidate_store.py tests/application/test_movie_navigator.py tests/movies/test_candidate_store.py
git commit -m "feat(navigator): carry public_id in stack entries and candidate refs"
```

---

## Task 13: Routes — `_resolve_public_id_or_404` shared helper

**Files:**
- Modify: `nextreel/web/routes/shared.py`
- Test: `tests/web/test_route_helpers.py` (or a new file)

- [ ] **Step 13.1: Write a failing test**

Create `tests/web/test_resolve_public_id.py`:

```python
"""Tests for the public_id → tconst route helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from quart.exceptions import HTTPException

from nextreel.web.routes.shared import _resolve_public_id_or_404


async def test_returns_tconst_for_known_id(app):
    async with app.test_request_context("/"):
        with patch("nextreel.web.routes.shared._services") as services:
            services.return_value.movie_manager.db_pool = AsyncMock()
            with patch(
                "nextreel.web.routes.shared.resolve_to_tconst",
                new=AsyncMock(return_value="tt0393109"),
            ):
                result = await _resolve_public_id_or_404("a8fk3j")
                assert result == "tt0393109"


async def test_aborts_404_for_unknown(app):
    async with app.test_request_context("/"):
        with patch("nextreel.web.routes.shared._services") as services:
            services.return_value.movie_manager.db_pool = AsyncMock()
            with patch(
                "nextreel.web.routes.shared.resolve_to_tconst",
                new=AsyncMock(return_value=None),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await _resolve_public_id_or_404("a8fk3j")
                assert exc_info.value.status_code == 404


async def test_aborts_404_for_invalid_format(app):
    async with app.test_request_context("/"):
        # No DB hit needed — format check rejects.
        with pytest.raises(HTTPException) as exc_info:
            await _resolve_public_id_or_404("tt0393109")
        assert exc_info.value.status_code == 404
```

- [ ] **Step 13.2: Run test to verify failure**

Run: `python3 -m pytest tests/web/test_resolve_public_id.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 13.3: Implement the helper in `nextreel/web/routes/shared.py`**

Add an import near the existing imports:

```python
from movies.public_id import _ID_RE as _PUBLIC_ID_RE, resolve_to_tconst
```

Add the helper function (near `_services` for proximity to its callers):

```python
async def _resolve_public_id_or_404(public_id: str) -> str:
    """Resolve a public_id from a route path to a tconst, or abort 404.

    Combines format validation and DB resolution so route handlers can
    write a single line to translate the URL identifier to the internal
    primary key.
    """
    if not isinstance(public_id, str) or not _PUBLIC_ID_RE.match(public_id):
        abort(404)
    services = _services()
    tconst = await resolve_to_tconst(services.movie_manager.db_pool, public_id)
    if tconst is None:
        abort(404)
    return tconst
```

Add `_resolve_public_id_or_404` and `_PUBLIC_ID_RE` to the module's
`__all__`.

- [ ] **Step 13.4: Run tests to verify pass**

Run: `python3 -m pytest tests/web/test_resolve_public_id.py -v`
Expected: PASS.

- [ ] **Step 13.5: Commit**

```bash
git add nextreel/web/routes/shared.py tests/web/test_resolve_public_id.py
git commit -m "feat(routes): add _resolve_public_id_or_404 helper"
```

---

## Task 14: `/api/projection-state/<public_id>` — switch route to public_id

**Files:**
- Modify: `nextreel/web/routes/search.py`
- Test: `tests/web/test_search_route.py`

- [ ] **Step 14.1: Write failing tests**

Append to `tests/web/test_search_route.py`:

```python
async def test_projection_state_resolves_public_id(test_client):
    """The route accepts a 6-char public_id and returns the projection state."""
    # Use the existing test fixture pattern in this file. Patch
    # resolve_to_tconst and the projection_store.select_row.
    from unittest.mock import AsyncMock, patch

    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value="tt0393109")):
        with patch("nextreel.web.routes.search._services") as services:
            services.return_value.movie_manager.projection_store.select_row = AsyncMock(
                return_value={"projection_state": "ready"}
            )
            response = await test_client.get("/api/projection-state/a8fk3j")
            assert response.status_code == 200
            body = await response.get_json()
            assert body == {"state": "ready"}


async def test_projection_state_404_for_imdb_tconst(test_client):
    """Old IMDb-shaped paths return 404, not 400."""
    response = await test_client.get("/api/projection-state/tt0393109")
    assert response.status_code == 404


async def test_projection_state_404_for_unknown_id(test_client):
    from unittest.mock import AsyncMock, patch
    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value=None)):
        response = await test_client.get("/api/projection-state/zzzzzz")
        assert response.status_code == 404
```

- [ ] **Step 14.2: Run tests to verify failure**

Run: `python3 -m pytest tests/web/test_search_route.py -v -k projection_state`
Expected: FAIL — current route uses `<tconst>` and returns 400.

- [ ] **Step 14.3: Update the route**

In `nextreel/web/routes/search.py`, replace the existing
`projection_state` handler with:

```python
from nextreel.web.routes.shared import _resolve_public_id_or_404


@bp.route("/api/projection-state/<public_id>", methods=["GET"])
@with_timeout(_REQUEST_TIMEOUT)
async def projection_state(public_id):
    """Lightweight projection state probe — used by the movie page poller
    to detect when background enrichment has completed so it can refresh
    into the fully-populated view.
    """
    tconst = await _resolve_public_id_or_404(public_id)
    services = _services()
    row = await services.movie_manager.projection_store.select_row(tconst)
    state = row.get("projection_state") if row else None
    return jsonify({"state": state})
```

Remove the now-unused `_TCONST_RE` import from this file (search.py) and
the `abort` import if it's no longer referenced.

- [ ] **Step 14.4: Run tests to verify pass**

Run: `python3 -m pytest tests/web/test_search_route.py -v`
Expected: PASS.

- [ ] **Step 14.5: Update the JS poller**

In `static/js/movie-card.js` (and any other client that polls
`/api/projection-state/`), change the URL construction to use the public
ID. Look for fetches like:

```js
fetch(`/api/projection-state/${tconst}`)
```

Replace with the page's public ID. The data attribute on the page body
will be renamed from `data-tconst` to `data-public-id` in Task 19.

For now (since we haven't yet swapped templates), update the JS to read
either `data-public-id` or fall back to `data-tconst` so we can ship this
task in isolation:

```js
const publicId = document.body.dataset.publicId || document.body.dataset.tconst;
fetch(`/api/projection-state/${publicId}`)
```

(The fallback gets removed in Task 19 once templates emit `data-public-id`.)

- [ ] **Step 14.6: Commit**

```bash
git add nextreel/web/routes/search.py static/js/movie-card.js tests/web/test_search_route.py
git commit -m "feat(api): switch /api/projection-state/<id> to public_id"
```

---

## Task 15: `/watched/{add,remove}/<public_id>` — switch route signatures

**Files:**
- Modify: `nextreel/web/routes/watched.py`
- Test: `tests/web/test_watched_route_delegation.py`

- [ ] **Step 15.1: Write failing tests**

Append to `tests/web/test_watched_route_delegation.py`:

```python
async def test_add_to_watched_resolves_public_id(test_client, logged_in_user):
    """POST /watched/add/<public_id> resolves the ID and inserts the right tconst."""
    from unittest.mock import AsyncMock, patch
    add_mock = AsyncMock()
    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value="tt0393109")):
        with patch("nextreel.web.routes.watched._services") as services:
            services.return_value.movie_manager.watched_store.add = add_mock
            response = await test_client.post(
                "/watched/add/a8fk3j",
                headers={"X-CSRF-Token": logged_in_user.csrf_token, "Accept": "application/json"},
            )
            assert response.status_code == 200
            add_mock.assert_awaited_once_with(logged_in_user.user_id, "tt0393109")


async def test_add_to_watched_404_for_imdb_path(test_client, logged_in_user):
    """Old /watched/add/tt0393109 path returns 404."""
    response = await test_client.post(
        "/watched/add/tt0393109",
        headers={"X-CSRF-Token": logged_in_user.csrf_token},
    )
    assert response.status_code == 404


async def test_remove_from_watched_resolves_public_id(test_client, logged_in_user):
    from unittest.mock import AsyncMock, patch
    remove_mock = AsyncMock()
    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value="tt0393109")):
        with patch("nextreel.web.routes.watched._services") as services:
            services.return_value.movie_manager.watched_store.remove = remove_mock
            response = await test_client.post(
                "/watched/remove/a8fk3j",
                headers={"X-CSRF-Token": logged_in_user.csrf_token, "Accept": "application/json"},
            )
            assert response.status_code == 200
            remove_mock.assert_awaited_once_with(logged_in_user.user_id, "tt0393109")
```

(If `logged_in_user` is not an existing fixture, look at the file's
existing tests to use whichever auth/CSRF pattern is already established
there.)

- [ ] **Step 15.2: Run tests to verify failure**

Run: `python3 -m pytest tests/web/test_watched_route_delegation.py -v -k public_id`
Expected: FAIL — current handler validates `_TCONST_RE` and returns 400.

- [ ] **Step 15.3: Update both handlers**

In `nextreel/web/routes/watched.py`, replace `_TCONST_RE` import with
`_resolve_public_id_or_404`:

```python
from nextreel.web.routes.shared import (
    LIST_VALID_SORTS,
    _current_user_id,
    _letterboxd_import_service,
    _require_login,
    _resolve_public_id_or_404,
    _services,
    _watched_list_presenter,
    _watched_progress_service,
    _wants_json_response,
    bp,
    logger,
    parse_list_filter_params,
    parse_list_pagination,
)
```

Replace the two handlers:

```python
@bp.route("/watched/add/<public_id>", methods=["POST"])
@csrf_required
@rate_limited("watched")
async def add_to_watched(public_id):
    tconst = await _resolve_public_id_or_404(public_id)
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watched_store.add(user_id, tconst)
    logger.info("User %s marked %s as watched", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_watched": True,
                "tconst": tconst,
                "public_id": public_id,
            }
        )

    return redirect(_safe_referrer(tconst), code=303)


@bp.route("/watched/remove/<public_id>", methods=["POST"])
@csrf_required
@rate_limited("watched")
async def remove_from_watched(public_id):
    tconst = await _resolve_public_id_or_404(public_id)
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watched_store.remove(user_id, tconst)
    logger.info("User %s removed %s from watched", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_watched": False,
                "tconst": tconst,
                "public_id": public_id,
            }
        )

    return redirect(_safe_referrer(tconst), code=303)
```

- [ ] **Step 15.4: Run tests to verify pass**

Run: `python3 -m pytest tests/web/test_watched_route_delegation.py -v`
Expected: PASS.

- [ ] **Step 15.5: Commit**

```bash
git add nextreel/web/routes/watched.py tests/web/test_watched_route_delegation.py
git commit -m "feat(watched): switch add/remove routes to public_id"
```

---

## Task 16: `/watchlist/{add,remove}/<public_id>` — switch route signatures

**Files:**
- Modify: `nextreel/web/routes/watchlist.py`
- Test: `tests/web/test_watchlist_routes.py`

- [ ] **Step 16.1: Write failing tests**

Append to `tests/web/test_watchlist_routes.py`:

```python
async def test_add_to_watchlist_resolves_public_id(test_client, logged_in_user):
    from unittest.mock import AsyncMock, patch
    add_mock = AsyncMock()
    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value="tt0393109")):
        with patch("nextreel.web.routes.watchlist._services") as services:
            services.return_value.movie_manager.watchlist_store.add = add_mock
            response = await test_client.post(
                "/watchlist/add/a8fk3j",
                headers={"X-CSRF-Token": logged_in_user.csrf_token, "Accept": "application/json"},
            )
            assert response.status_code == 200
            add_mock.assert_awaited_once_with(logged_in_user.user_id, "tt0393109")


async def test_remove_from_watchlist_resolves_public_id(test_client, logged_in_user):
    from unittest.mock import AsyncMock, patch
    remove_mock = AsyncMock()
    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value="tt0393109")):
        with patch("nextreel.web.routes.watchlist._services") as services:
            services.return_value.movie_manager.watchlist_store.remove = remove_mock
            response = await test_client.post(
                "/watchlist/remove/a8fk3j",
                headers={"X-CSRF-Token": logged_in_user.csrf_token, "Accept": "application/json"},
            )
            assert response.status_code == 200
            remove_mock.assert_awaited_once_with(logged_in_user.user_id, "tt0393109")


async def test_add_to_watchlist_404_for_imdb_path(test_client, logged_in_user):
    response = await test_client.post(
        "/watchlist/add/tt0393109",
        headers={"X-CSRF-Token": logged_in_user.csrf_token},
    )
    assert response.status_code == 404
```

- [ ] **Step 16.2: Run tests to verify failure**

Run: `python3 -m pytest tests/web/test_watchlist_routes.py -v -k public_id`
Expected: FAIL.

- [ ] **Step 16.3: Update both handlers**

In `nextreel/web/routes/watchlist.py`, swap the import (replace
`_TCONST_RE` with `_resolve_public_id_or_404`) and update the two handler
signatures + bodies, mirroring the pattern from Task 15:

```python
@bp.route("/watchlist/add/<public_id>", methods=["POST"])
@csrf_required
@rate_limited("watchlist")
async def add_to_watchlist(public_id):
    tconst = await _resolve_public_id_or_404(public_id)
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watchlist_store.add(user_id, tconst)
    logger.info("User %s added %s to watchlist", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_in_watchlist": True,
                "tconst": tconst,
                "public_id": public_id,
            }
        )

    return redirect(_safe_referrer(tconst), code=303)


@bp.route("/watchlist/remove/<public_id>", methods=["POST"])
@csrf_required
@rate_limited("watchlist")
async def remove_from_watchlist(public_id):
    tconst = await _resolve_public_id_or_404(public_id)
    user_id = _current_user_id()
    if not user_id:
        abort(401, "Login required")

    services = _services()
    await services.movie_manager.watchlist_store.remove(user_id, tconst)
    logger.info("User %s removed %s from watchlist", user_id, tconst)
    if _wants_json_response():
        return jsonify(
            {
                "ok": True,
                "is_in_watchlist": False,
                "tconst": tconst,
                "public_id": public_id,
            }
        )

    return redirect(_safe_referrer(tconst), code=303)
```

- [ ] **Step 16.4: Run tests to verify pass**

Run: `python3 -m pytest tests/web/test_watchlist_routes.py -v`
Expected: PASS.

- [ ] **Step 16.5: Commit**

```bash
git add nextreel/web/routes/watchlist.py tests/web/test_watchlist_routes.py
git commit -m "feat(watchlist): switch add/remove routes to public_id"
```

---

## Task 17: `/movie/<slug_with_id>` — switch detail route to public_id with canonical-redirect

**Files:**
- Modify: `nextreel/web/routes/movies.py`
- Test: `tests/web/test_routes_extended.py` (or wherever movie-detail tests live)

- [ ] **Step 17.1: Write failing tests**

Create or append to a movie-detail test file (e.g.
`tests/web/test_movie_detail_route.py`):

```python
"""Tests for the /movie/<slug_with_id> route."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


async def test_movie_detail_renders_when_slug_canonical(test_client):
    payload = {
        "tconst": "tt0393109",
        "title": "The Departed",
        "primaryTitle": "The Departed",
        "year": "2006",
        "public_id": "a8fk3j",
        "_full": True,
    }
    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value="tt0393109")):
        with patch("nextreel.web.routes.movies._movie_detail_service") as svc:
            svc.get = AsyncMock(return_value=type("VM", (), {
                "movie": payload,
                "previous_count": 0,
                "is_watched": False,
                "is_in_watchlist": False,
            })())
            response = await test_client.get("/movie/the-departed-2006-a8fk3j")
            assert response.status_code == 200


async def test_movie_detail_redirects_to_canonical_on_slug_mismatch(test_client):
    payload = {
        "tconst": "tt0393109",
        "primaryTitle": "The Departed",
        "year": "2006",
        "public_id": "a8fk3j",
        "_full": True,
    }
    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value="tt0393109")):
        with patch("nextreel.web.routes.movies._movie_detail_service") as svc:
            svc.get = AsyncMock(return_value=type("VM", (), {
                "movie": payload,
                "previous_count": 0,
                "is_watched": False,
                "is_in_watchlist": False,
            })())
            response = await test_client.get("/movie/wrong-slug-a8fk3j")
            assert response.status_code == 301
            assert response.headers["Location"].endswith("/movie/the-departed-2006-a8fk3j")


async def test_movie_detail_404_for_imdb_tconst_url(test_client):
    """Old /movie/tt0393109 URLs return 404 (hard break, no redirect)."""
    response = await test_client.get("/movie/tt0393109")
    assert response.status_code == 404


async def test_movie_detail_404_for_unknown_id(test_client):
    with patch("nextreel.web.routes.shared.resolve_to_tconst", new=AsyncMock(return_value=None)):
        response = await test_client.get("/movie/anything-aaaaaa")
        assert response.status_code == 404
```

- [ ] **Step 17.2: Run tests to verify failure**

Run: `python3 -m pytest tests/web/test_movie_detail_route.py -v`
Expected: FAIL.

- [ ] **Step 17.3: Rewrite the `movie_detail` handler**

In `nextreel/web/routes/movies.py`, replace the entire handler with:

```python
from quart import abort, g, redirect, render_template

from infra.route_helpers import with_timeout
from movies.landing_film_service import fetch_random_landing_film
from movies.movie_url import build_movie_path, parse_movie_path, title_slug
from nextreel.web.routes.shared import (
    _REQUEST_TIMEOUT,
    _current_state,
    _current_user_id,
    _legacy_session,
    _movie_detail_service,
    _movie_image_context,
    _resolve_public_id_or_404,
    _services,
    bp,
    logger,
)


@bp.route("/movie/<slug_with_id>")
@with_timeout(_REQUEST_TIMEOUT)
async def movie_detail(slug_with_id):
    parsed = parse_movie_path(slug_with_id)
    if parsed is None:
        abort(404)
    requested_slug, public_id = parsed

    tconst = await _resolve_public_id_or_404(public_id)

    state = _current_state()
    user_id = _current_user_id()
    services = _services()
    movie_manager = services.movie_manager

    logger.debug(
        "Fetching movie details for public_id: %s (tconst=%s), session_id: %s. "
        "Correlation ID: %s",
        public_id,
        tconst,
        state.session_id,
        g.correlation_id,
    )

    view_model = await _movie_detail_service.get(
        movie_manager=movie_manager,
        state=state,
        user_id=user_id,
        tconst=tconst,
    )

    if view_model is None:
        logger.info("No data found for movie with public_id: %s (tconst=%s)", public_id, tconst)
        abort(404)

    if not view_model.movie.get("_full"):
        logger.warning(
            "Rendering partial movie detail for %s (projection_state=%s)",
            tconst,
            view_model.movie.get("projection_state"),
        )

    canonical_slug = title_slug(
        view_model.movie.get("primaryTitle") or view_model.movie.get("title"),
        view_model.movie.get("year"),
    )
    if requested_slug != canonical_slug:
        return redirect(
            build_movie_path(
                view_model.movie.get("primaryTitle") or view_model.movie.get("title"),
                view_model.movie.get("year"),
                public_id,
            ),
            code=301,
        )

    g.is_watched = view_model.is_watched
    g.is_in_watchlist = view_model.is_in_watchlist
    image_context = _movie_image_context(view_model.movie)
    return await render_template(
        "movie.html",
        movie=view_model.movie,
        previous_count=view_model.previous_count,
        public_id=public_id,
        **image_context,
    )
```

(Also leave the existing `home` route and `_LANDING_FALLBACK_POOL` in
place — they don't need to change yet; landing-fallback URL building is
covered in Task 18.)

- [ ] **Step 17.4: Run tests to verify pass**

Run: `python3 -m pytest tests/web/test_movie_detail_route.py -v`
Expected: PASS.

- [ ] **Step 17.5: Commit**

```bash
git add nextreel/web/routes/movies.py tests/web/test_movie_detail_route.py
git commit -m "feat(routes): switch /movie/<slug_with_id> to public_id with canonical redirect"
```

---

## Task 18: Outbound URL building — replace `url_for("main.movie_detail", tconst=...)` calls

**Files:**
- Modify: `nextreel/web/routes/navigation.py`
- Modify: `nextreel/web/routes/shared.py`
- Modify: `nextreel/web/routes/movies.py` (the landing fallback pool)
- Test: `tests/web/test_routes_navigation.py`

- [ ] **Step 18.1: Write failing tests**

Append to `tests/web/test_routes_navigation.py`:

```python
async def test_next_movie_redirects_to_public_id_path(test_client, logged_in_user):
    """The next_movie outcome redirect must use /movie/<slug>-<public_id>, not tconst."""
    from unittest.mock import AsyncMock, patch
    outcome = type("Out", (), {"tconst": "tt0393109", "state_conflict": False})()
    with patch("nextreel.web.routes.navigation._services") as services:
        services.return_value.movie_manager.next_movie = AsyncMock(return_value=outcome)
        services.return_value.movie_manager.projection_store.select_row = AsyncMock(
            return_value={
                "tconst": "tt0393109",
                "public_id": "a8fk3j",
                "payload_json": '{"primaryTitle": "The Departed", "year": "2006"}',
            }
        )
        response = await test_client.post(
            "/next_movie",
            headers={"X-CSRF-Token": logged_in_user.csrf_token},
        )
        assert response.status_code == 303
        assert response.headers["Location"].endswith("/movie/the-departed-2006-a8fk3j")
```

- [ ] **Step 18.2: Run test to verify failure**

Run: `python3 -m pytest tests/web/test_routes_navigation.py -v -k public_id_path`
Expected: FAIL — current code calls `url_for("main.movie_detail", tconst=...)` and the route no longer accepts that kwarg.

- [ ] **Step 18.3: Update `_redirect_for_navigation_outcome` in `shared.py`**

Add a helper to `nextreel/web/routes/shared.py` that loads the projection
row and builds the canonical path:

```python
from movies.movie_url import build_movie_path
from movies.public_id import public_id_for_tconst


async def _build_movie_url_for_tconst(tconst: str, *, query: dict | None = None) -> str:
    """Look up the projection row for ``tconst`` and build the canonical URL.

    Falls back to ``/`` if the projection has no public_id yet (a transient
    state during the rollout, after which assertion at startup guarantees
    every row has one).
    """
    services = _services()
    projection = await services.movie_manager.projection_store.select_row(tconst)
    if not projection:
        return url_for("main.home")
    public_id = projection.get("public_id")
    if not public_id:
        # Last-resort fallback during the rollout window.
        public_id = await public_id_for_tconst(services.movie_manager.db_pool, tconst)
        if not public_id:
            return url_for("main.home")

    payload = projection.get("payload_json")
    if isinstance(payload, str):
        import json as _json
        payload = _json.loads(payload) if payload else {}
    if not isinstance(payload, dict):
        payload = {}
    title = payload.get("primaryTitle") or payload.get("title")
    year = payload.get("year")
    path = build_movie_path(title, year, public_id)
    if query:
        from urllib.parse import urlencode
        path = f"{path}?{urlencode(query)}"
    return path
```

Replace `_redirect_for_navigation_outcome`:

```python
async def _redirect_for_navigation_outcome(outcome: NavigationOutcome):
    if outcome.state_conflict:
        if outcome.tconst:
            url = await _build_movie_url_for_tconst(
                outcome.tconst, query={"state_conflict": "1"}
            )
            return redirect(url, code=303)
        return redirect(url_for("main.home", state_conflict=1), code=303)
    if outcome.tconst:
        await _schedule_prefetch(outcome.tconst)
        url = await _build_movie_url_for_tconst(outcome.tconst)
        return redirect(url, code=303)
    abort(500, description="Navigation outcome missing target movie")
```

Add `_build_movie_url_for_tconst` to `__all__`.

- [ ] **Step 18.4: Update `nextreel/web/routes/navigation.py`**

Replace direct `url_for("main.movie_detail", ...)` calls. Specifically,
in `previous_movie`:

```python
    if outcome is None:
        tconst = movie_manager.get_current_movie_tconst(state)
        if tconst:
            url = await _build_movie_url_for_tconst(tconst)
            return redirect(url)
        return redirect(url_for("main.home"))
```

And in `filtered_movie_endpoint`:

```python
    if outcome is not None:
        if wants_json:
            if outcome.tconst:
                url = await _build_movie_url_for_tconst(outcome.tconst)
                return jsonify({"ok": True, "redirect": url})
            return _no_matches_response()
        return await _redirect_for_navigation_outcome(outcome)
    if wants_json:
        return _no_matches_response()
    tconst = movie_manager.get_current_movie_tconst(state)
    if tconst:
        url = await _build_movie_url_for_tconst(tconst)
        return redirect(url, code=303)
    return redirect(url_for("main.home"), code=303)
```

(Add `_build_movie_url_for_tconst` to the imports from `shared`.)

- [ ] **Step 18.5: Update the landing fallback pool in `movies.py`**

The hard-coded landing fallback pool currently uses `tconst` keys but is
linked from a template that generates `/movie/...` URLs from those
keys. Update each entry in `_LANDING_FALLBACK_POOL` to also include a
`public_id` and `year` so the home template can generate a valid URL
(see Task 20 for the template change). Add a key `public_id` to each
fallback entry — pick a 6-char ID per entry (since they're verified
movies, generate stable IDs by hand) e.g.:

```python
_LANDING_FALLBACK_POOL = (
    {
        "tconst": "tt0062622",
        "public_id": "lf0001",   # landing fallback static IDs
        "title": "2001: A Space Odyssey",
        "primaryTitle": "2001: A Space Odyssey",
        "year": "1968",
        ...
    },
    ...
)
```

(Note: when these IDs reach the production `movie_projection` table during
backfill, they'll be reassigned to whatever 6-char value the random
generator produces. The fallback pool only renders when the SQL landing
query returns nothing, which should be never in production. To make the
fallback URLs actually resolve, ensure the seeded fallback IDs match the
backfilled IDs by *not* hard-coding them — instead, build the URL
dynamically. Simpler approach: change the home template to call
`movie_url(landing_film)` once Task 20 lands; until then, the fallback
links point at home until a real projection-backed landing film is
available.)

Concretely for this task: don't pre-pick `public_id` values. Instead, in
the home route handler (`home` in `nextreel/web/routes/movies.py`), if
the chosen landing film has no `public_id`, look it up:

```python
async def home():
    state = _current_state()
    services = _services()
    data = await services.movie_manager.home(state, legacy_session=_legacy_session())

    landing_film = await fetch_random_landing_film(services.movie_manager.db_pool)
    if landing_film is None:
        landing_film = random.choice(_LANDING_FALLBACK_POOL)

    if isinstance(landing_film, dict) and not landing_film.get("public_id"):
        # Fallback-pool entries don't carry a public_id; resolve it lazily.
        landing_film = dict(landing_film)
        landing_film["public_id"] = await public_id_for_tconst(
            services.movie_manager.db_pool, landing_film.get("tconst")
        )

    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
        landing_film=landing_film,
    )
```

Add the import: `from movies.public_id import public_id_for_tconst`.

- [ ] **Step 18.6: Run tests to verify pass**

Run: `python3 -m pytest tests/web/test_routes_navigation.py tests/web/test_routes_home.py -v`
Expected: PASS.

- [ ] **Step 18.7: Commit**

```bash
git add nextreel/web/routes/shared.py nextreel/web/routes/navigation.py nextreel/web/routes/movies.py tests/web/test_routes_navigation.py
git commit -m "feat(routes): build /movie/<slug>-<public_id> URLs in outbound redirects"
```

---

## Task 19: Jinja global `movie_url(movie)` and template updates

**Files:**
- Modify: `nextreel/web/routes/shared.py` (register the global in `init_routes`)
- Modify: `templates/movie_card.html`
- Modify: `templates/_watched_card.html`
- Modify: `templates/_watchlist_card.html`
- Modify: `templates/home.html`
- Modify: `templates/movie.html`
- Modify: `static/js/movie-card.js`
- Modify: `static/js/watchlist-toggle.js` (if it reads `data-tconst`)
- Test: `tests/web/test_routes_home.py` and others affected

- [ ] **Step 19.1: Write a failing test for the Jinja global**

Append to `tests/web/test_app.py` (or create `tests/web/test_jinja_globals.py`):

```python
async def test_movie_url_global_registered(app):
    """The Jinja global movie_url(movie) is registered and produces correct paths."""
    from movies.movie_url import build_movie_path
    movie_url = app.jinja_env.globals.get("movie_url")
    assert movie_url is not None
    movie = {"primaryTitle": "The Departed", "year": "2006", "public_id": "a8fk3j"}
    assert movie_url(movie) == "/movie/the-departed-2006-a8fk3j"
```

(Use whatever fixture makes the production-shaped app available — likely
the same one used in `test_app.py`.)

- [ ] **Step 19.2: Run test to verify failure**

Run: `python3 -m pytest tests/web/test_app.py -v -k movie_url_global`
Expected: FAIL — `movie_url` not registered.

- [ ] **Step 19.3: Register the Jinja global**

In `nextreel/web/routes/shared.py`, replace `init_routes`:

```python
def _movie_url_global(movie: dict) -> str:
    """Jinja global: return the canonical /movie/... URL for a movie dict.

    Returns ``/`` if the dict is missing a public_id (defensive fallback).
    """
    if not movie:
        return "/"
    public_id = movie.get("public_id")
    if not public_id:
        return "/"
    title = movie.get("primaryTitle") or movie.get("title")
    year = movie.get("year") or movie.get("startYear")
    from movies.movie_url import build_movie_path
    return build_movie_path(title, year, public_id)


def init_routes(app, movie_manager, metrics_collector):
    app.extensions["nextreel"] = NextReelServices(
        movie_manager=movie_manager,
        metrics_collector=metrics_collector,
    )
    app.jinja_env.filters["language_name"] = language_name
    app.jinja_env.globals["movie_url"] = _movie_url_global
```

Add `_movie_url_global` to `__all__`.

- [ ] **Step 19.4: Run test to verify pass**

Run: `python3 -m pytest tests/web/test_app.py -v -k movie_url_global`
Expected: PASS.

- [ ] **Step 19.5: Update templates to use `movie_url` and bare public_id for POSTs**

`templates/movie_card.html` — replace tconst-based URL building:

```jinja
{# OLD: {% set watch_tconst = movie.tconst or movie.imdb_id %} #}
{% set watch_id = movie.public_id %}
{% set movie_href = movie_url(movie) %}
```

Then:

- Replace every `/watchlist/{add,remove}/{{ watch_tconst }}` with
  `/watchlist/{add,remove}/{{ watch_id }}` (4 occurrences around line 226–231).
- Replace every `/watched/{add,remove}/{{ watch_tconst }}` with
  `/watched/{add,remove}/{{ watch_id }}` (4 occurrences around line 258–263).
- Replace `next='/movie/' ~ watch_tconst` (lines 247 and 279) with
  `next=movie_href`.

`templates/_watchlist_card.html` — change line 1:

```jinja
{% set href = movie_url(movie) %}
```

`templates/_watched_card.html` — change line 1 the same way.

`templates/home.html` — change the landing-film CTA (around line 72):

```jinja
<a class="landing-cta-ghost" href="{{ movie_url(landing_film) }}">See this film ↗</a>
```

`templates/movie.html` — change the body data attribute (line 52):

```jinja
<body class="bg-surface text-body"
      data-projection-state="{{ movie.projection_state or '' }}"
      data-public-id="{{ movie.public_id or public_id or '' }}">
```

(`public_id` is passed from the route handler in Task 17.)

- [ ] **Step 19.6: Update JS to read `data-public-id`**

In `static/js/movie-card.js`, replace any reference to `data-tconst` /
`dataset.tconst` with `data-public-id` / `dataset.publicId`. Update the
projection-state poll URL to use the public ID:

```js
const publicId = document.body.dataset.publicId || "";
fetch(`/api/projection-state/${publicId}`)
```

Remove the fallback to `dataset.tconst` introduced in Task 14.

In `static/js/watchlist-toggle.js`, the toggle URLs are baked into the
form's `action`/`data-add-url`/`data-remove-url` (Task 19.5 already
updated them to use `watch_id`). The JS should read those data attributes
verbatim — no further change needed unless it does string surgery on
`tconst`. If you find code constructing URLs from tconst, swap to
`data-public-id` similarly.

- [ ] **Step 19.7: Run all template/route tests**

Run: `python3 -m pytest tests/web/ -v`
Expected: PASS — including any existing template-bound tests that now
hit `movie_url` instead of `url_for(... tconst=...)`. Some tests may have
hard-coded `/movie/tt...` URLs in fixtures; update those to the new shape
where they appear.

- [ ] **Step 19.8: Commit**

```bash
git add nextreel/web/routes/shared.py templates/ static/js/ tests/web/
git commit -m "feat(templates): switch movie URLs to movie_url() Jinja global"
```

---

## Task 20: Movie-dict contract guard test

**Files:**
- Create: `tests/web/test_movie_dict_contract.py`

This is the test the spec calls out as "the broken-link guard": every
code path that hands a movie dict to a template must include `public_id`
and `primaryTitle`.

- [ ] **Step 20.1: Write the test**

Create `tests/web/test_movie_dict_contract.py`:

```python
"""Guards the contract that movie dicts handed to templates carry public_id.

Missing this field would yield a silently-broken link in templates (the
``movie_url`` Jinja global returns ``/`` when public_id is absent), with
no runtime error to surface the bug. This test asserts the contract at
each producer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


REQUIRED_KEYS = {"public_id", "primaryTitle"}


def _assert_movie_dict(d: dict, *, where: str) -> None:
    missing = REQUIRED_KEYS - d.keys()
    assert not missing, f"{where} produced a movie dict missing keys: {missing}"


async def test_route_services_movie_detail_view_model_contract():
    from nextreel.web.route_services import MovieDetailService
    from types import SimpleNamespace

    payload = {
        "tconst": "tt0393109",
        "title": "The Departed",
        "primaryTitle": "The Departed",
        "year": "2006",
        "public_id": "a8fk3j",
        "_full": True,
    }
    movie_manager = SimpleNamespace(
        projection_store=SimpleNamespace(
            fetch_renderable_payload=AsyncMock(return_value=payload)
        ),
        watched_store=SimpleNamespace(is_watched=AsyncMock(return_value=False)),
        watchlist_store=SimpleNamespace(is_in_watchlist=AsyncMock(return_value=False)),
        prev_stack_length=lambda state: 0,
    )
    vm = await MovieDetailService().get(
        movie_manager=movie_manager,
        state=SimpleNamespace(),
        user_id=None,
        tconst="tt0393109",
    )
    _assert_movie_dict(vm.movie, where="MovieDetailService")


def test_projection_repository_payload_from_row_contract():
    from movies.projection_repository import ProjectionRepository
    repo = ProjectionRepository(db_pool=None)
    payload = repo.payload_from_row({
        "tconst": "tt0393109",
        "payload_json": '{"primaryTitle": "The Departed"}',
        "projection_state": "ready",
        "public_id": "a8fk3j",
    })
    _assert_movie_dict(payload, where="ProjectionRepository.payload_from_row")


def test_navigator_movie_ref_carries_public_id():
    from nextreel.application.movie_navigator import _movie_ref
    ref = _movie_ref({
        "tconst": "tt0393109",
        "title": "The Departed",
        "primaryTitle": "The Departed",
        "public_id": "a8fk3j",
    })
    # _movie_ref is the lightweight shape — it must include public_id so
    # downstream URL building doesn't lose the canonical key.
    assert ref.get("public_id") == "a8fk3j"
```

(Add additional producers — landing-film service, watched-list query
shape, watchlist-list query shape — as separate tests in the same file
following the same pattern. Use AsyncMock fixtures matching each
producer's contract.)

- [ ] **Step 20.2: Run the contract test**

Run: `python3 -m pytest tests/web/test_movie_dict_contract.py -v`
Expected: PASS (all earlier tasks ensured the keys are populated).

If any test fails, that producer is missing a `public_id` propagation —
fix it where the test points (e.g. add `public_id` to the SELECT, copy
it through the dict construction).

- [ ] **Step 20.3: Commit**

```bash
git add tests/web/test_movie_dict_contract.py
git commit -m "test: assert public_id contract on every movie-dict producer"
```

---

## Task 21: Pre-deploy NULL-rows assertion

**Files:**
- Modify: `nextreel/web/app.py` (or `nextreel/web/lifecycle.py`)
- Test: `tests/web/test_app_bootstrap_boundaries.py`

After backfill is supposed to have run, refusing to start when any
projection row still has NULL `public_id` prevents serving broken links.
This task adds that startup check.

- [ ] **Step 21.1: Write a failing test**

Append to `tests/web/test_app_bootstrap_boundaries.py`:

```python
async def test_startup_aborts_when_null_public_id_rows_exist():
    """If backfill didn't run, the app refuses to start."""
    from unittest.mock import AsyncMock

    from infra.runtime_schema import assert_no_null_public_ids
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value={"null_count": 5})

    with pytest.raises(RuntimeError, match="public_id"):
        await assert_no_null_public_ids(pool)


async def test_startup_passes_when_no_null_public_ids():
    from unittest.mock import AsyncMock

    from infra.runtime_schema import assert_no_null_public_ids
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value={"null_count": 0})

    # Should not raise.
    await assert_no_null_public_ids(pool)
```

- [ ] **Step 21.2: Run tests to verify failure**

Run: `python3 -m pytest tests/web/test_app_bootstrap_boundaries.py -v -k null_public_id`
Expected: FAIL with `ImportError`.

- [ ] **Step 21.3: Implement the assertion helper**

Append to `infra/runtime_schema.py`:

```python
async def assert_no_null_public_ids(db_pool) -> None:
    """Refuse to start when any movie_projection row has a NULL public_id.

    The backfill helper should have populated every row before the code
    cutover. If any NULLs remain at startup, the app would render broken
    URLs (the ``movie_url`` Jinja global returns ``/`` when public_id is
    missing), so we'd rather refuse to start than ship a degraded UI.
    """
    row = await db_pool.execute(
        "SELECT COUNT(*) AS null_count FROM movie_projection WHERE public_id IS NULL",
        fetch="one",
    )
    null_count = (
        row.get("null_count") if isinstance(row, dict) else (row[0] if row else 0)
    )
    if null_count and int(null_count) > 0:
        raise RuntimeError(
            f"Refusing to start: {null_count} movie_projection rows have "
            f"NULL public_id. Run the backfill helper first."
        )
```

- [ ] **Step 21.4: Wire it into the lifecycle hook**

Add the call in the existing `before_serving` hook (likely
`nextreel/web/lifecycle.py` or `nextreel/web/app.py` —
`grep -n "before_serving\|ensure_runtime_schema" nextreel/web/`). After
the `ensure_runtime_schema` call, add:

```python
from infra.runtime_schema import assert_no_null_public_ids
...
await ensure_runtime_schema(movie_manager.db_pool)
await assert_no_null_public_ids(movie_manager.db_pool)
```

- [ ] **Step 21.5: Run tests to verify pass**

Run: `python3 -m pytest tests/web/test_app_bootstrap_boundaries.py -v`
Expected: PASS.

- [ ] **Step 21.6: Commit**

```bash
git add infra/runtime_schema.py nextreel/web/app.py nextreel/web/lifecycle.py tests/web/test_app_bootstrap_boundaries.py
git commit -m "feat(startup): assert no NULL public_id rows before serving"
```

---

## Task 22: Dead-URL counter for `tt…` 404s

**Files:**
- Modify: `infra/metrics.py` (or wherever counters are declared)
- Modify: `nextreel/web/app.py` (or wherever the 404 handler lives)
- Test: `tests/infra/test_metrics_collector.py`

- [ ] **Step 22.1: Write a failing test**

Append to `tests/infra/test_metrics_collector.py`:

```python
async def test_tt_url_404_counter_increments_on_imdb_path_404(test_client):
    from infra.metrics import tt_url_404_total
    before = tt_url_404_total._value.get() if hasattr(tt_url_404_total, "_value") else 0
    response = await test_client.get("/movie/tt0393109")
    assert response.status_code == 404
    after = tt_url_404_total._value.get() if hasattr(tt_url_404_total, "_value") else 0
    assert after >= before + 1
```

(Adapt the counter-reading to whichever Prometheus client API the project
uses; the existing `infra/metrics.py` will show the pattern.)

- [ ] **Step 22.2: Run test to verify failure**

Run: `python3 -m pytest tests/infra/test_metrics_collector.py -v -k tt_url_404`
Expected: FAIL — `tt_url_404_total` doesn't exist.

- [ ] **Step 22.3: Add the counter and wire it into the 404 handler**

In `infra/metrics.py`, add:

```python
from prometheus_client import Counter

tt_url_404_total = Counter(
    "nextreel_tt_url_404_total",
    "404 responses for legacy /movie/tt... or /watched/.../tt... URLs.",
)
```

In the app's 404 error handler (find it via
`grep -n "errorhandler\|def not_found\|@app.errorhandler" nextreel/web/`),
detect the legacy pattern and increment:

```python
import re as _re

_LEGACY_TT_PATH_RE = _re.compile(r"/(?:movie|watched/(?:add|remove)|watchlist/(?:add|remove)|api/projection-state)/tt\d+")


@app.errorhandler(404)
async def _on_404(error):
    from quart import request
    if _LEGACY_TT_PATH_RE.search(request.path):
        from infra.metrics import tt_url_404_total
        try:
            tt_url_404_total.inc()
        except Exception:  # noqa: BLE001
            pass
    return "Not found", 404
```

(If a 404 handler already exists, integrate the counter into it instead
of replacing it.)

- [ ] **Step 22.4: Run tests to verify pass**

Run: `python3 -m pytest tests/infra/test_metrics_collector.py -v`
Expected: PASS.

- [ ] **Step 22.5: Commit**

```bash
git add infra/metrics.py nextreel/web/app.py tests/infra/test_metrics_collector.py
git commit -m "feat(metrics): count 404s for legacy /movie/tt... paths"
```

---

## Task 23: Retire `_TCONST_RE` from `shared.py`

**Files:**
- Modify: `nextreel/web/routes/shared.py`
- Test: by passing existing tests (no new tests).

- [ ] **Step 23.1: Find remaining references**

Run: `grep -rn "_TCONST_RE" nextreel/ tests/ | grep -v __pycache__`

By this task, the route handlers in `movies.py`, `watched.py`,
`watchlist.py`, and `search.py` all use `_resolve_public_id_or_404`. Any
remaining references should be in tests that still hit the constant for
asserting historical behavior — those should also have been replaced in
their respective tasks.

- [ ] **Step 23.2: Remove the constant and its export**

In `nextreel/web/routes/shared.py`:
- Delete the line: `_TCONST_RE = re.compile(r"^tt\d{1,10}$")`.
- Remove `"_TCONST_RE"` from `__all__`.

If the only `re` usage in the file was for `_TCONST_RE`, you can also
drop `import re`. Verify with: `grep -n "^import re\|re\\." nextreel/web/routes/shared.py`.

- [ ] **Step 23.3: Run the full web-test suite**

Run: `python3 -m pytest tests/web/ -v`
Expected: PASS — no references remain.

If a test fails on `ImportError`, it's still importing `_TCONST_RE` —
update that test to use `_PUBLIC_ID_RE` (or remove the assertion if it
no longer applies).

- [ ] **Step 23.4: Commit**

```bash
git add nextreel/web/routes/shared.py
git commit -m "refactor(routes): retire _TCONST_RE; routes now resolve via public_id"
```

---

## Task 24: Final integration sweep — run everything

**Files:**
- None (validation pass)

- [ ] **Step 24.1: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — all tests across infra, movies, web, application,
session, integration, structure, and workers.

If anything fails:
1. Read the failure carefully.
2. The most common failure mode at this stage is a hard-coded
   `/movie/tt...` URL in a test fixture. Update such fixtures to the new
   slug-with-id shape. Use any existing public_id from the test DB or
   generate one with `movies.public_id.generate`.
3. The second most common failure mode is a missing `public_id` in a
   mock movie dict. Add it.

- [ ] **Step 24.2: Run lint and format**

```bash
black . --line-length 100
flake8 . --exclude=venv,node_modules
```

Expected: clean.

- [ ] **Step 24.3: Smoke-test the dev server**

```bash
python3 app.py
```

In another terminal, hit:
- `curl -i http://127.0.0.1:5000/movie/tt0393109` — expect 404.
- Visit `http://127.0.0.1:5000/` in a browser, click around, verify URLs
  look like `/movie/the-departed-2006-a8fk3j`.
- Verify the watched/watchlist toggles still POST and respond with the
  expected JSON.
- Verify the projection-state poll on the movie page.

Stop the server.

- [ ] **Step 24.4: Final commit if any test-fixture cleanup was needed**

```bash
git status
# If any uncommitted fixture/test edits remain:
git add tests/
git commit -m "test: update fixtures to public_id URLs"
```

---

## Self-review checklist

- [x] **Spec coverage:**
  - Decision 1 (URL-only scope) — Tasks 5, 6, 7, 8 keep tconst as PK; only `public_id` added.
  - Decision 2 (Reddit-style URL) — Task 4 (`build_movie_path`), Task 17 (route).
  - Decision 3 (6-char `[a-z0-9]`) — Task 1 (alphabet/length).
  - Decision 4 (random + retry) — Task 2 (`assign_public_id`).
  - Decision 5 (404 hard break) — Task 17 (no `tt…` route handler), Task 22 (counter).
  - Decision 6 (all 5 routes switch) — Tasks 14, 15, 16, 17.
  - Decision 7 (slug computed, not stored) — Task 4 (`title_slug` is pure).
  - Decision 8 (bare ID for non-detail routes) — Tasks 14, 15, 16.
  - Decision 9 (no Redis cache) — not implemented (intentional).
  - Decision 10 (two-deploy phasing) — Tasks 5+6 ship first, then everything from Task 7 onwards forms the second deploy.
  - Schema (Section 1) — Task 5.
  - Public ID module (Section 2) — Tasks 1, 2, 3.
  - URL building (Section 3) — Task 4.
  - Route changes (Section 4) — Tasks 13–18.
  - Worker enrichment (Section 5) — Task 7.
  - Backfill & rollout (Section 6) — Task 6, plus Task 21 (NULL-rows assertion) and Task 22 (legacy-URL counter).
  - Testing — every task includes its own tests; Task 20 is the contract guard.

- [x] **No placeholders:** no TBD/TODO; every step has the actual code.

- [x] **Type/method consistency:**
  - `assign_public_id(pool, tconst) -> str | None` (Task 2) ↔ Task 7 calls match.
  - `resolve_to_tconst(pool, public_id) -> str | None` (Task 3) ↔ Task 13/14 helper match.
  - `_resolve_public_id_or_404(public_id) -> str` (Task 13) ↔ Tasks 14/15/16/17 callers match.
  - `build_movie_path(title, year, public_id) -> str` (Task 4) ↔ Task 18/19 callers match.
  - `parse_movie_path(slug_with_id) -> tuple[str, str] | None` (Task 4) ↔ Task 17 caller matches.
  - `_movie_url_global(movie) -> str` (Task 19) reads `movie.public_id` and `movie.primaryTitle/title` and `movie.year/startYear`.
  - Required keys on movie dicts: `public_id`, `primaryTitle` — guarded by Task 20.

- [x] **Phase boundary clear:** Tasks 1–6 form the schema-only deploy; Tasks 7+ form the code-cutover deploy. Production safe to roll back between.
