"""Unit tests for movies.public_id."""

from __future__ import annotations

import re

import pytest

from movies.public_id import ID_ALPHABET, ID_LENGTH, ID_RE, generate


class TestGenerate:
    def test_returns_six_chars(self):
        result = generate()
        assert len(result) == ID_LENGTH == 6

    def test_uses_only_lowercase_alphanumeric(self):
        for _ in range(50):
            result = generate()
            assert all(ch in ID_ALPHABET for ch in result)
            assert re.fullmatch(r"[a-z0-9]{6}", result)

    def test_varies_across_calls(self):
        # 50 generations should produce >40 distinct values (collisions
        # extremely improbable at 36^6 = 2.18B combos).
        results = {generate() for _ in range(50)}
        assert len(results) > 40


class TestIdRegex:
    def test_accepts_valid_id(self):
        assert ID_RE.match("a8fk3j")
        assert ID_RE.match("000000")
        assert ID_RE.match("zzzzzz")

    def test_rejects_imdb_tconst(self):
        assert not ID_RE.match("tt0393109")

    def test_rejects_uppercase(self):
        assert not ID_RE.match("A8FK3J")
        assert not ID_RE.match("a8FK3j")

    def test_rejects_wrong_length(self):
        assert not ID_RE.match("a8fk3")     # 5 chars
        assert not ID_RE.match("a8fk3jx")   # 7 chars
        assert not ID_RE.match("")

    def test_rejects_special_chars(self):
        assert not ID_RE.match("a8fk3-")
        assert not ID_RE.match("a8 k3j")
        assert not ID_RE.match("a8fk3!")


from unittest.mock import AsyncMock

import pymysql
import pytest

from movies.public_id import (
    PublicIdGenerationError,
    MAX_GENERATION_ATTEMPTS,
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
        ] * MAX_GENERATION_ATTEMPTS

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

    async def test_retries_on_pool_wrapped_duplicate_key(self, fake_pool):
        """Production parity: the pool wraps IntegrityError as DatabaseError(...) from exc.

        Regression guard for the bug where ``except IntegrityError`` would never
        fire because ``infra/pool.execute`` re-raises every non-DatabaseError
        exception as ``DatabaseError(...) from original_exc``. The retry must
        unwrap ``__cause__`` to detect the 1062 errno.
        """
        from infra.errors import DatabaseError

        original = pymysql.err.IntegrityError(
            1062, "Duplicate entry 'aaaaaa' for key 'uq_movie_projection_public_id'"
        )
        wrapped = DatabaseError(f"Query failed: {original}")
        wrapped.__cause__ = original

        fake_pool.execute.side_effect = [
            {"public_id": None},
            wrapped,
            1,
        ]

        result = await assign_public_id(fake_pool, "tt0393109")

        assert len(result) == 6
        assert fake_pool.execute.await_count == 3

    async def test_returns_none_when_row_vanishes_mid_flight(self, fake_pool):
        """Affected=0 + re-read finds no row → row was deleted between SELECT/UPDATE.

        Returns None so the caller's 404 path can take over. A warning is
        logged so a sudden burst of these is visible in production.
        """
        fake_pool.execute.side_effect = [
            {"public_id": None},  # initial SELECT — row exists, no public_id
            0,                    # UPDATE — affected nothing (row deleted in between)
            None,                 # re-read — no row
        ]

        result = await assign_public_id(fake_pool, "tt0393109")

        assert result is None
        assert fake_pool.execute.await_count == 3

    async def test_returns_winning_value_when_concurrent_writer_assigned(self, fake_pool):
        """Affected=0 + re-read finds a public_id → another writer won the race.

        Returns the winning value rather than re-attempting (the row is no
        longer NULL, so further attempts would return 0 forever).
        """
        fake_pool.execute.side_effect = [
            {"public_id": None},
            0,
            {"public_id": "winn3r"},
        ]

        result = await assign_public_id(fake_pool, "tt0393109")

        assert result == "winn3r"
        assert fake_pool.execute.await_count == 3


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
