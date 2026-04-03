"""Extended session security tests — fingerprint mismatch, absolute timeout, and decorator."""

from datetime import datetime, timedelta, timezone

import pytest
from quart import Quart, session

from session.keys import (
    SESSION_CREATED_KEY,
    SESSION_FINGERPRINT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_ROTATION_COUNT_KEY,
    SESSION_TOKEN_KEY,
)
from session.security import EnhancedSessionSecurity, require_secure_session


@pytest.fixture
def app():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    app.config["SESSION_ROTATION_INTERVAL"] = 10
    app.config["SESSION_IDLE_TIMEOUT_MINUTES"] = 15
    app.config["MAX_SESSION_DURATION_HOURS"] = 8
    return app


@pytest.fixture
def security(app):
    return EnhancedSessionSecurity(app)


class TestFingerprintMismatch:
    """Session should be rejected when device fingerprint changes."""

    @pytest.mark.asyncio
    async def test_different_user_agent_invalidates_session(self, app, security):
        # Create session with one UA
        async with app.test_request_context(
            "/", headers={"User-Agent": "OriginalBrowser/1.0"}
        ):
            await security.create_session()
            saved_session = dict(session)

        # Validate with different UA
        async with app.test_request_context(
            "/", headers={"User-Agent": "DifferentBrowser/2.0"}
        ):
            for key, val in saved_session.items():
                session[key] = val
            result = await security.validate_session()
            assert result is False

    @pytest.mark.asyncio
    async def test_same_user_agent_validates(self, app, security):
        async with app.test_request_context(
            "/", headers={"User-Agent": "SameBrowser/1.0"}
        ):
            await security.create_session()
            result = await security.validate_session()
            assert result is True


class TestAbsoluteTimeout:
    """Session should be rejected when maximum duration is exceeded."""

    @pytest.mark.asyncio
    async def test_expired_by_max_duration(self, app, security):
        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            await security.create_session()
            # Set created_at to 9 hours ago (max is 8)
            old = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
            session[SESSION_CREATED_KEY] = old
            # Keep last_activity recent so idle timeout doesn't trigger
            session[SESSION_LAST_ACTIVITY_KEY] = datetime.now(timezone.utc).isoformat()

            result = await security.validate_session()
            assert result is False

    @pytest.mark.asyncio
    async def test_within_max_duration(self, app, security):
        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            await security.create_session()
            # Set created_at to 7 hours ago (within 8h max)
            recent = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
            session[SESSION_CREATED_KEY] = recent

            result = await security.validate_session()
            assert result is True


class TestMissingFields:
    """Session with missing required fields should be rejected."""

    @pytest.mark.asyncio
    async def test_missing_token(self, app, security):
        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            await security.create_session()
            del session[SESSION_TOKEN_KEY]
            assert await security.validate_session() is False

    @pytest.mark.asyncio
    async def test_missing_fingerprint(self, app, security):
        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            await security.create_session()
            del session[SESSION_FINGERPRINT_KEY]
            assert await security.validate_session() is False

    @pytest.mark.asyncio
    async def test_missing_created_at(self, app, security):
        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            await security.create_session()
            del session[SESSION_CREATED_KEY]
            assert await security.validate_session() is False


class TestTokenRotation:
    """Token rotation fires at the configured interval."""

    @pytest.mark.asyncio
    async def test_no_rotation_before_interval(self, app, security):
        app.config["SESSION_ROTATION_INTERVAL"] = 5
        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            await security.create_session()
            original_token = session[SESSION_TOKEN_KEY]

            # 4 updates — should NOT rotate (interval is 5)
            for _ in range(4):
                await security.update_session_activity()

            assert session[SESSION_TOKEN_KEY] == original_token
            assert session[SESSION_ROTATION_COUNT_KEY] == 4

    @pytest.mark.asyncio
    async def test_rotation_at_interval(self, app, security):
        app.config["SESSION_ROTATION_INTERVAL"] = 3
        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            await security.create_session()
            original_token = session[SESSION_TOKEN_KEY]

            for _ in range(3):
                await security.update_session_activity()

            assert session[SESSION_TOKEN_KEY] != original_token
            assert session[SESSION_ROTATION_COUNT_KEY] == 0


class TestRequireSecureSessionDecorator:
    """The @require_secure_session decorator rejects invalid sessions."""

    @pytest.mark.asyncio
    async def test_rejects_when_no_token(self, app, security):
        app.config["_session_security"] = security

        @require_secure_session
        async def protected():
            return "secret"

        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            from werkzeug.exceptions import Unauthorized

            with pytest.raises(Unauthorized):
                await protected()

    @pytest.mark.asyncio
    async def test_passes_with_valid_session(self, app, security):
        app.config["_session_security"] = security

        @require_secure_session
        async def protected():
            return "secret"

        async with app.test_request_context(
            "/", headers={"User-Agent": "TestBrowser/1.0"}
        ):
            await security.create_session()
            result = await protected()
            assert result == "secret"


class TestParseTimestamp:
    """_parse_timestamp edge cases."""

    def test_none_returns_none(self):
        assert EnhancedSessionSecurity._parse_timestamp(None) is None

    def test_empty_string_returns_none(self):
        assert EnhancedSessionSecurity._parse_timestamp("") is None

    def test_invalid_format_returns_none(self):
        assert EnhancedSessionSecurity._parse_timestamp("not-a-date") is None

    def test_valid_iso_returns_datetime(self):
        result = EnhancedSessionSecurity._parse_timestamp("2026-01-01T12:00:00+00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026
