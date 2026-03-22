import pytest
from quart import Quart, session

from session_keys import (
    FINGERPRINT_COMPONENTS_KEY,
    SESSION_CREATED_KEY,
    SESSION_FINGERPRINT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_ROTATION_COUNT_KEY,
    SESSION_TOKEN_KEY,
)
from session_security_enhanced import EnhancedSessionSecurity


@pytest.fixture
def app():
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    app.config["SESSION_ROTATION_INTERVAL"] = 2
    app.config["SESSION_IDLE_TIMEOUT_MINUTES"] = 15
    app.config["MAX_SESSION_DURATION_HOURS"] = 8
    return app


@pytest.fixture
def security_manager(app):
    return EnhancedSessionSecurity(app)


@pytest.mark.asyncio
async def test_create_session_sets_required_keys(app, security_manager):
    async with app.test_request_context("/", headers={"User-Agent": "TestBrowser/1.0"}):
        created = await security_manager.create_session()

        assert SESSION_TOKEN_KEY in session
        assert SESSION_FINGERPRINT_KEY in session
        assert SESSION_CREATED_KEY in session
        assert SESSION_LAST_ACTIVITY_KEY in session
        assert SESSION_ROTATION_COUNT_KEY in session
        assert FINGERPRINT_COMPONENTS_KEY in session
        assert created["token"] == session[SESSION_TOKEN_KEY]


@pytest.mark.asyncio
async def test_validate_session_rejects_idle_timeout(app, security_manager):
    async with app.test_request_context("/", headers={"User-Agent": "TestBrowser/1.0"}):
        await security_manager.create_session()
        session[SESSION_LAST_ACTIVITY_KEY] = "2000-01-01T00:00:00+00:00"

        assert await security_manager.validate_session() is False


@pytest.mark.asyncio
async def test_update_session_activity_rotates_token(app, security_manager):
    app.config["SESSION_ROTATION_INTERVAL"] = 1

    async with app.test_request_context("/", headers={"User-Agent": "TestBrowser/1.0"}):
        await security_manager.create_session()
        old_token = session[SESSION_TOKEN_KEY]

        await security_manager.update_session_activity()

        assert session[SESSION_TOKEN_KEY] != old_token
        assert session[SESSION_ROTATION_COUNT_KEY] == 0


@pytest.mark.asyncio
async def test_destroy_session_clears_state(app, security_manager):
    async with app.test_request_context("/", headers={"User-Agent": "TestBrowser/1.0"}):
        await security_manager.create_session()
        await security_manager.destroy_session()

        assert SESSION_TOKEN_KEY not in session
        assert SESSION_FINGERPRINT_KEY not in session


@pytest.mark.asyncio
async def test_trusted_proxy_fingerprint_uses_forwarded_ip(app, security_manager, monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1")
    headers = {
        "User-Agent": "TestBrowser/1.0",
        "Accept-Language": "en-US",
        "X-Forwarded-For": "203.0.113.5",
    }

    async with app.test_request_context(
        "/",
        headers=headers,
        scope_base={"client": ("10.0.0.1", 1234)},
    ):
        _, components = security_manager.generate_device_fingerprint()
        assert components["ip"] == "203.0.113.5"


@pytest.mark.asyncio
async def test_untrusted_proxy_ignores_forwarded_ip(app, security_manager, monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    headers = {
        "User-Agent": "TestBrowser/1.0",
        "X-Forwarded-For": "203.0.113.5",
    }

    async with app.test_request_context(
        "/",
        headers=headers,
        scope_base={"client": ("10.0.0.9", 1234)},
    ):
        _, components = security_manager.generate_device_fingerprint()
        assert components["ip"] == "10.0.0.9"
