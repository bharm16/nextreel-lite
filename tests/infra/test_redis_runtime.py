"""Tests for infra.redis_runtime."""

from __future__ import annotations

import pytest

from infra.redis_runtime import resolve_redis_url


class TestResolveRedisUrl:
    def test_uses_REDIS_URL_when_set_in_production(self):
        env = {"REDIS_URL": "rediss://prod:port"}
        assert resolve_redis_url(environment="production", environ=env) == "rediss://prod:port"

    def test_builds_upstash_url_from_host_port_password(self):
        env = {
            "UPSTASH_REDIS_HOST": "fly.upstash.io",
            "UPSTASH_REDIS_PORT": "6379",
            "UPSTASH_REDIS_PASSWORD": "secret",
        }
        url = resolve_redis_url(environment="production", environ=env)
        assert url == "rediss://:secret@fly.upstash.io:6379"

    def test_raises_when_password_missing_in_production(self):
        # When host/port are set but password is None, the previous implementation
        # silently produced "rediss://:None@host:port" — the literal string "None"
        # — which then sent AUTH None to the server.
        env = {
            "UPSTASH_REDIS_HOST": "fly.upstash.io",
            "UPSTASH_REDIS_PORT": "6379",
        }
        with pytest.raises(RuntimeError, match="UPSTASH_REDIS_PASSWORD"):
            resolve_redis_url(environment="production", environ=env)

    def test_raises_when_host_or_port_missing_in_production(self):
        env = {"UPSTASH_REDIS_PASSWORD": "secret"}
        with pytest.raises(RuntimeError, match="REDIS_URL or UPSTASH_REDIS_HOST"):
            resolve_redis_url(environment="production", environ=env)

    def test_falls_back_to_localhost_in_development(self):
        env: dict[str, str] = {}
        assert resolve_redis_url(environment="development", environ=env) == "redis://localhost:6379"
