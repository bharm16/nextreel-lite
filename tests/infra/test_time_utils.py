"""Tests for env_int and env_float helpers in infra.time_utils."""

import os
from unittest.mock import patch

from infra.time_utils import (
    _reset_current_year_cache,
    current_year,
    env_float,
    env_int,
)


class TestCurrentYear:
    def test_current_year_caches(self):
        _reset_current_year_cache()
        y1 = current_year()
        y2 = current_year()
        assert y1 == y2

    def test_current_year_matches_datetime(self):
        _reset_current_year_cache()
        from datetime import datetime, timezone

        assert current_year() == datetime.now(timezone.utc).year


class TestEnvInt:
    def test_unset_returns_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert env_int("FOO_NOT_SET", 42) == 42

    def test_valid_value(self):
        with patch.dict(os.environ, {"FOO": "7"}):
            assert env_int("FOO", 0) == 7

    def test_empty_string_returns_default(self):
        with patch.dict(os.environ, {"FOO": ""}):
            assert env_int("FOO", 5) == 5

    def test_whitespace_only_returns_default(self):
        with patch.dict(os.environ, {"FOO": "   "}):
            assert env_int("FOO", 5) == 5

    def test_strips_whitespace(self):
        with patch.dict(os.environ, {"FOO": "  12  "}):
            assert env_int("FOO", 0) == 12

    def test_invalid_returns_default(self):
        with patch.dict(os.environ, {"FOO": "notanumber"}):
            assert env_int("FOO", 9) == 9

    def test_negative(self):
        with patch.dict(os.environ, {"FOO": "-3"}):
            assert env_int("FOO", 0) == -3

    def test_zero(self):
        with patch.dict(os.environ, {"FOO": "0"}):
            assert env_int("FOO", 5) == 0


class TestEnvFloat:
    def test_unset_returns_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert env_float("FOO_NOT_SET", 3.14) == 3.14

    def test_valid(self):
        with patch.dict(os.environ, {"FOO": "0.25"}):
            assert env_float("FOO", 1.0) == 0.25

    def test_invalid_returns_default(self):
        with patch.dict(os.environ, {"FOO": "bad"}):
            assert env_float("FOO", 3.14) == 3.14

    def test_strips_whitespace(self):
        with patch.dict(os.environ, {"FOO": "  0.5 "}):
            assert env_float("FOO", 0.0) == 0.5

    def test_empty_returns_default(self):
        with patch.dict(os.environ, {"FOO": ""}):
            assert env_float("FOO", 1.5) == 1.5
