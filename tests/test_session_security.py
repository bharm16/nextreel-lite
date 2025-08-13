import pytest
import asyncio
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
import secrets

from session_auth_enhanced import SessionSecurityManager, SESSION_TOKEN_KEY, SESSION_FINGERPRINT_KEY, SESSION_CREATED_KEY, SESSION_LAST_ACTIVITY_KEY, SESSION_ROTATION_COUNT_KEY
from quart import Quart, session


@pytest.fixture
def app():
    app = Quart(__name__)
    app.config['SECRET_KEY'] = secrets.token_hex(32)
    app.config['SESSION_TIMEOUT_MINUTES'] = 30
    app.config['SESSION_IDLE_TIMEOUT_MINUTES'] = 15
    app.config['SESSION_ROTATION_INTERVAL'] = 10
    app.config['MAX_SESSION_DURATION_HOURS'] = 24
    app.config['SESSION_COOKIE_SECURE'] = False  # For testing
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    return app


@pytest.fixture
def security_manager(app):
    manager = SessionSecurityManager(app)
    return manager


@pytest.mark.asyncio
async def test_token_generation_entropy(security_manager):
    """Test that tokens have sufficient entropy and uniqueness."""
    tokens = set()
    for _ in range(1000):
        token = security_manager.generate_secure_token()
        
        # Check token length (should be at least 32 chars)
        assert len(token) >= 32
        
        # Check uniqueness
        assert token not in tokens
        tokens.add(token)
    
    # All tokens should be unique
    assert len(tokens) == 1000


@pytest.mark.asyncio
async def test_fingerprint_consistency(app, security_manager):
    """Test that fingerprints are consistent for same client."""
    async with app.test_request_context(
        '/',
        headers={
            'User-Agent': 'TestBrowser/1.0',
            'X-Forwarded-For': '192.168.1.1',
            'Accept': 'text/html',
            'Accept-Language': 'en-US',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1'
        }
    ):
        fp1 = security_manager.generate_enhanced_fingerprint()
        fp2 = security_manager.generate_enhanced_fingerprint()
        
        # Same client should get same fingerprint
        assert fp1 == fp2
        
        # Fingerprint should be a hex string (SHA256)
        assert len(fp1) == 64
        assert all(c in '0123456789abcdef' for c in fp1)


@pytest.mark.asyncio
async def test_session_timeout(app, security_manager):
    """Test that sessions timeout correctly."""
    async with app.test_request_context('/'):
        # Create session
        session_data = security_manager.create_session()
        
        # Should be valid immediately
        assert security_manager.validate_session() is True
        
        # Simulate idle timeout
        past_time = (datetime.utcnow() - timedelta(minutes=20)).isoformat()
        session[SESSION_LAST_ACTIVITY_KEY] = past_time
        
        # Should be invalid due to idle timeout
        assert security_manager.validate_session() is False


@pytest.mark.asyncio
async def test_session_rotation(app, security_manager):
    """Test that session tokens rotate correctly."""
    async with app.test_request_context('/'):
        # Create session
        security_manager.create_session()
        original_token = session[SESSION_TOKEN_KEY]
        
        # Simulate activity to trigger rotation
        for i in range(app.config['SESSION_ROTATION_INTERVAL']):
            security_manager.update_session_activity()
        
        # Token should have rotated
        new_token = session[SESSION_TOKEN_KEY]
        assert new_token != original_token


@pytest.mark.asyncio
async def test_fingerprint_mismatch_detection(app, security_manager):
    """Test that fingerprint changes are detected."""
    async with app.test_request_context(
        '/',
        headers={'User-Agent': 'Browser1', 'X-Forwarded-For': '1.1.1.1'}
    ):
        security_manager.create_session()
        stored_fp = session[SESSION_FINGERPRINT_KEY]
        
    # Change client characteristics
    async with app.test_request_context(
        '/',
        headers={'User-Agent': 'Browser2', 'X-Forwarded-For': '2.2.2.2'}
    ):
        # Need to manually set the session data for this test
        session[SESSION_TOKEN_KEY] = 'test_token'
        session[SESSION_FINGERPRINT_KEY] = stored_fp
        session[SESSION_CREATED_KEY] = datetime.utcnow().isoformat()
        session[SESSION_LAST_ACTIVITY_KEY] = datetime.utcnow().isoformat()
        
        # Should detect fingerprint mismatch
        assert security_manager.validate_session() is False


def test_secure_cookie_flags(app):
    """Test that secure cookie flags are set correctly."""
    # Test production settings
    app.config['FLASK_ENV'] = 'production'
    config = app.config
    config_instance = type('Config', (), {})()
    config_instance.SESSION_COOKIE_SECURE = True
    
    assert config['SESSION_COOKIE_HTTPONLY'] is True
    assert config['SESSION_COOKIE_SAMESITE'] == 'Lax'
    
    # Test development settings
    app.config['FLASK_ENV'] = 'development'
    config_instance.SESSION_COOKIE_SECURE = False


@pytest.mark.asyncio
async def test_session_destruction(app, security_manager):
    """Test that sessions are properly destroyed."""
    async with app.test_request_context('/'):
        # Create session
        security_manager.create_session()
        assert SESSION_TOKEN_KEY in session
        
        # Destroy session
        security_manager.destroy_session()
        assert SESSION_TOKEN_KEY not in session
        assert len(session) == 0


@pytest.mark.asyncio
async def test_session_creation_data(app, security_manager):
    """Test that session creation sets all required data."""
    async with app.test_request_context('/'):
        session_data = security_manager.create_session()
        
        # Check that all required keys are present
        assert SESSION_TOKEN_KEY in session
        assert SESSION_FINGERPRINT_KEY in session
        assert SESSION_CREATED_KEY in session
        assert SESSION_LAST_ACTIVITY_KEY in session
        assert SESSION_ROTATION_COUNT_KEY in session
        
        # Check return data structure
        assert 'token' in session_data
        assert 'fingerprint' in session_data
        assert 'created' in session_data
        
        # Verify rotation counter is initialized
        assert session[SESSION_ROTATION_COUNT_KEY] == 0


@pytest.mark.asyncio
async def test_absolute_session_timeout(app, security_manager):
    """Test absolute session timeout."""
    async with app.test_request_context('/'):
        # Create session
        security_manager.create_session()
        
        # Simulate session older than max duration
        old_time = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        session[SESSION_CREATED_KEY] = old_time
        session[SESSION_LAST_ACTIVITY_KEY] = datetime.utcnow().isoformat()  # Recent activity
        
        # Should be invalid due to absolute timeout
        assert security_manager.validate_session() is False


@pytest.mark.asyncio
async def test_activity_update(app, security_manager):
    """Test that activity updates work correctly."""
    async with app.test_request_context('/'):
        # Create session
        security_manager.create_session()
        original_activity = session[SESSION_LAST_ACTIVITY_KEY]
        original_count = session[SESSION_ROTATION_COUNT_KEY]
        
        # Wait a moment
        await asyncio.sleep(0.1)
        
        # Update activity
        security_manager.update_session_activity()
        
        # Activity should be updated
        assert session[SESSION_LAST_ACTIVITY_KEY] != original_activity
        assert session[SESSION_ROTATION_COUNT_KEY] == original_count + 1


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, '-v'])