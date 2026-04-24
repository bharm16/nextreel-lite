"""Tests for config layer — env var fallback chain, SSL, defaults."""

import importlib
import os
from unittest.mock import patch

import pytest


class TestDatabaseConfig:
    """config.database.DatabaseConfig environment handling."""

    def _reimport(self):
        """Re-import config.database after resetting the env cache."""
        from config.env import _reset_environment

        _reset_environment()
        import config.database as mod

        importlib.reload(mod)
        return mod.DatabaseConfig

    def test_defaults_to_production(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NEXTREEL_ENV", None)
            os.environ.pop("FLASK_ENV", None)
            Config = self._reimport()
            # Production config uses PROD_DB_HOST first
            config = Config.get_db_config()
            assert config["host"] == "127.0.0.1"  # fallback default

    def test_nextreel_env_takes_precedence(self):
        with patch.dict(
            os.environ,
            {
                "NEXTREEL_ENV": "development",
                "FLASK_ENV": "production",
            },
        ):
            Config = self._reimport()
            # Should be development config (no PROD_ prefix lookups)
            config = Config.get_db_config()
            assert config["host"] == os.getenv("DB_HOST", "127.0.0.1")

    def test_flask_env_fallback(self):
        with patch.dict(os.environ, {"FLASK_ENV": "development"}, clear=False):
            os.environ.pop("NEXTREEL_ENV", None)
            Config = self._reimport()
            config = Config.get_db_config()
            # Development config
            assert "host" in config

    def test_production_uses_prod_env_vars(self):
        with patch.dict(
            os.environ,
            {
                "NEXTREEL_ENV": "production",
                "PROD_DB_HOST": "prod.db.example.com",
                "PROD_DB_USER": "produser",
                "PROD_DB_PASSWORD": "prodpass",
                "PROD_DB_NAME": "proddb",
            },
        ):
            Config = self._reimport()
            config = Config.get_db_config()
            assert config["host"] == "prod.db.example.com"
            assert config["user"] == "produser"
            assert config["database"] == "proddb"

    def test_ssl_disabled_in_development(self):
        with patch.dict(os.environ, {"NEXTREEL_ENV": "development"}):
            Config = self._reimport()
            assert Config.use_ssl() is False

    def test_ssl_enabled_in_production(self):
        with patch.dict(os.environ, {"NEXTREEL_ENV": "production"}):
            Config = self._reimport()
            assert Config.use_ssl() is True

    def test_db_use_ssl_override_disables_in_production(self):
        with patch.dict(
            os.environ, {"NEXTREEL_ENV": "production", "DB_USE_SSL": "false"}
        ):
            Config = self._reimport()
            assert Config.use_ssl() is False

    def test_db_use_ssl_override_enables_in_development(self):
        with patch.dict(
            os.environ, {"NEXTREEL_ENV": "development", "DB_USE_SSL": "true"}
        ):
            Config = self._reimport()
            assert Config.use_ssl() is True

    def test_ssl_cert_path_from_env(self):
        with patch.dict(os.environ, {"SSL_CERT_PATH": "/certs/ca.pem"}):
            Config = self._reimport()
            assert Config.get_ssl_cert_path() == "/certs/ca.pem"

    def test_ssl_cert_path_default_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SSL_CERT_PATH", None)
            Config = self._reimport()
            assert Config.get_ssl_cert_path() is None

    def test_port_from_env(self):
        with patch.dict(
            os.environ,
            {
                "NEXTREEL_ENV": "development",
                "DB_PORT": "3307",
            },
        ):
            Config = self._reimport()
            config = Config.get_db_config()
            assert config["port"] == 3307


class TestSessionConfig:
    """config.session.SessionConfig environment handling."""

    def _reimport(self):
        from config.env import _reset_environment

        _reset_environment()
        import config.session as mod

        importlib.reload(mod)
        return mod.SessionConfig

    def test_secure_cookie_in_production(self):
        with patch.dict(os.environ, {"NEXTREEL_ENV": "production"}):
            Config = self._reimport()
            sc = Config()
            assert sc.SESSION_COOKIE_SECURE is True

    def test_insecure_cookie_in_development(self):
        with patch.dict(os.environ, {"NEXTREEL_ENV": "development"}):
            Config = self._reimport()
            sc = Config()
            assert sc.SESSION_COOKIE_SECURE is False

    def test_cookie_domain_none_in_development(self):
        with patch.dict(os.environ, {"NEXTREEL_ENV": "development"}):
            Config = self._reimport()
            assert Config().SESSION_COOKIE_DOMAIN is None

    def test_cookie_httponly_always_true(self):
        Config = self._reimport()
        assert Config.SESSION_COOKIE_HTTPONLY is True

    def test_cookie_samesite_lax(self):
        Config = self._reimport()
        assert Config.SESSION_COOKIE_SAMESITE == "Lax"
