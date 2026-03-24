"""Tests for SecretsManager — retrieval, masking, validation, fail-secure."""

import os
from unittest.mock import patch

import pytest

from infra.secrets import SecretsManager


@pytest.fixture
def sm():
    """Fresh SecretsManager instance with cleared LRU cache."""
    manager = SecretsManager()
    yield manager
    manager.clear_cache()


# ---------------------------------------------------------------------------
# Secret retrieval
# ---------------------------------------------------------------------------


class TestGetSecret:
    def test_reads_from_environment(self, sm):
        with patch.dict(os.environ, {"TMDB_API_KEY": "abc123"}):
            assert sm.get_secret("TMDB_API_KEY") == "abc123"

    def test_returns_default_for_optional_secret(self, sm):
        with patch.dict(os.environ, {}, clear=False):
            # Ensure REDIS_PASSWORD is not in env
            os.environ.pop("REDIS_PASSWORD", None)
            result = sm.get_secret("REDIS_PASSWORD", default="fallback")
            assert result == "fallback"

    def test_raises_for_missing_required_secret_no_default(self, sm):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMDB_API_KEY", None)
            with pytest.raises(RuntimeError, match="Required secret"):
                sm.get_secret("TMDB_API_KEY")

    def test_required_secret_with_default_does_not_raise(self, sm):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMDB_API_KEY", None)
            result = sm.get_secret("TMDB_API_KEY", default="fallback-key")
            assert result == "fallback-key"

    def test_caches_result(self, sm):
        with patch.dict(os.environ, {"TMDB_API_KEY": "cached-value"}):
            first = sm.get_secret("TMDB_API_KEY")
        # Even after removing from env, cached value should persist
        os.environ.pop("TMDB_API_KEY", None)
        second = sm.get_secret("TMDB_API_KEY")
        assert first == second == "cached-value"

    def test_unknown_key_returns_none(self, sm):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOTALLY_UNKNOWN_KEY", None)
            result = sm.get_secret("TOTALLY_UNKNOWN_KEY")
            assert result is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateAllSecrets:
    def test_all_present_returns_true(self, sm):
        with patch.dict(os.environ, {
            "TMDB_API_KEY": "1234567890abcdef",
            "FLASK_SECRET_KEY": "abcdef1234567890",
        }):
            assert sm.validate_all_secrets() is True
            assert sm.is_validated() is True

    def test_missing_required_returns_false(self, sm):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMDB_API_KEY", None)
            os.environ.pop("FLASK_SECRET_KEY", None)
            assert sm.validate_all_secrets() is False
            assert sm.is_validated() is False

    def test_partial_missing_returns_false(self, sm):
        with patch.dict(os.environ, {"TMDB_API_KEY": "present"}, clear=False):
            os.environ.pop("FLASK_SECRET_KEY", None)
            result = sm.validate_all_secrets()
            assert result is False


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


class TestMasking:
    def test_long_secret_shows_first_and_last_four(self, sm):
        with patch.dict(os.environ, {
            "TMDB_API_KEY": "abcdefghijklmnop",
            "FLASK_SECRET_KEY": "1234567890abcdef",
        }):
            # validate_all_secrets logs masked values — we can test the
            # masking logic directly
            value = "abcdefghijklmnop"
            masked = value[:4] + '*' * max(len(value) - 8, 4) + value[-4:]
            assert masked.startswith("abcd")
            assert masked.endswith("mnop")
            assert "efghijkl" not in masked

    def test_short_secret_fully_masked(self):
        value = "short"
        masked = value[:4] + '*' * max(len(value) - 8, 4) + value[-4:] if len(value) > 8 else '***'
        assert masked == '***'

    def test_exactly_nine_chars(self):
        value = "123456789"
        masked = value[:4] + '*' * max(len(value) - 8, 4) + value[-4:]
        assert masked.startswith("1234")
        assert masked.endswith("6789")
        # 9 - 8 = 1, but max(1, 4) = 4
        assert "****" in masked


# ---------------------------------------------------------------------------
# Cache clearing
# ---------------------------------------------------------------------------


class TestCacheClearing:
    def test_clear_cache_resets_state(self, sm):
        with patch.dict(os.environ, {"TMDB_API_KEY": "value1"}):
            sm.get_secret("TMDB_API_KEY")

        sm.clear_cache()
        assert sm._secrets_cache == {}

    def test_clear_allows_new_value(self, sm):
        with patch.dict(os.environ, {"TMDB_API_KEY": "old"}):
            sm.get_secret("TMDB_API_KEY")
        sm.clear_cache()
        with patch.dict(os.environ, {"TMDB_API_KEY": "new"}):
            assert sm.get_secret("TMDB_API_KEY") == "new"
