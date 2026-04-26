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
