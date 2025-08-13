import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json
import base64

from quart import request, session, current_app
from werkzeug.exceptions import Unauthorized

# Session configuration constants
SESSION_TOKEN_KEY = "session_token"
SESSION_FINGERPRINT_KEY = "session_fp"
SESSION_CREATED_KEY = "session_created"
SESSION_LAST_ACTIVITY_KEY = "session_last_activity"
SESSION_ROTATION_COUNT_KEY = "session_rotation_count"

# Security settings
SESSION_TIMEOUT_MINUTES = 30  # Absolute timeout
SESSION_IDLE_TIMEOUT_MINUTES = 15  # Idle timeout
SESSION_ROTATION_INTERVAL = 10  # Rotate token every N requests
MAX_SESSION_DURATION_HOURS = 24  # Maximum session lifetime


class SessionSecurityManager:
    """Enhanced session security with multiple protection layers."""
    
    def __init__(self, app=None):
        self.app = app
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """Initialize the session security manager with the app."""
        self.app = app
        # Add configuration
        app.config.setdefault('SESSION_TIMEOUT_MINUTES', SESSION_TIMEOUT_MINUTES)
        app.config.setdefault('SESSION_IDLE_TIMEOUT_MINUTES', SESSION_IDLE_TIMEOUT_MINUTES)
        app.config.setdefault('SESSION_ROTATION_INTERVAL', SESSION_ROTATION_INTERVAL)
        app.config.setdefault('MAX_SESSION_DURATION_HOURS', MAX_SESSION_DURATION_HOURS)
    
    def generate_secure_token(self) -> str:
        """Generate a cryptographically secure session token with high entropy."""
        # Use 256 bits of entropy (32 bytes)
        random_bytes = secrets.token_bytes(32)
        
        # Add timestamp for uniqueness
        timestamp = str(time.time()).encode()
        
        # Add process ID for additional entropy
        pid = str(os.getpid()).encode()
        
        # Combine all entropy sources
        combined = random_bytes + timestamp + pid
        
        # Hash the combined entropy
        token_hash = hashlib.sha256(combined).digest()
        
        # Return URL-safe base64 encoded token
        return base64.urlsafe_b64encode(token_hash).decode('utf-8').rstrip('=')
    
    def generate_enhanced_fingerprint(self) -> str:
        """Generate enhanced fingerprint with multiple entropy sources."""
        components = []
        
        # User agent
        user_agent = request.headers.get("User-Agent", "unknown")
        components.append(user_agent)
        
        # IP address (consider both direct and forwarded)
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip:
            # Take first IP if multiple (comma-separated)
            ip = ip.split(',')[0].strip()
        components.append(ip or "unknown")
        
        # Accept headers for browser fingerprinting
        accept = request.headers.get("Accept", "")
        components.append(accept)
        
        # Accept-Language header
        accept_language = request.headers.get("Accept-Language", "")
        components.append(accept_language)
        
        # Accept-Encoding header
        accept_encoding = request.headers.get("Accept-Encoding", "")
        components.append(accept_encoding)
        
        # DNT (Do Not Track) header
        dnt = request.headers.get("DNT", "")
        components.append(dnt)
        
        # Add secret key for HMAC
        secret_key = current_app.config.get('SECRET_KEY', '')
        
        # Create HMAC-SHA256 fingerprint
        fingerprint_data = "|".join(components)
        fingerprint = hmac.new(
            secret_key.encode(),
            fingerprint_data.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return fingerprint
    
    def validate_session(self) -> bool:
        """Validate the current session with multiple checks."""
        # Check if session exists
        if SESSION_TOKEN_KEY not in session:
            return False
        
        # Validate fingerprint
        current_fp = self.generate_enhanced_fingerprint()
        stored_fp = session.get(SESSION_FINGERPRINT_KEY)
        
        if stored_fp != current_fp:
            # Log potential session hijacking attempt
            current_app.logger.warning(
                f"Session fingerprint mismatch. Possible hijacking attempt. "
                f"Session token: {session.get(SESSION_TOKEN_KEY)[:8]}..."
            )
            return False
        
        # Check session creation time (absolute timeout)
        created = session.get(SESSION_CREATED_KEY)
        if created:
            created_time = datetime.fromisoformat(created)
            max_duration = timedelta(hours=current_app.config['MAX_SESSION_DURATION_HOURS'])
            if datetime.utcnow() - created_time > max_duration:
                current_app.logger.info("Session expired: exceeded maximum duration")
                return False
        
        # Check idle timeout
        last_activity = session.get(SESSION_LAST_ACTIVITY_KEY)
        if last_activity:
            last_time = datetime.fromisoformat(last_activity)
            idle_timeout = timedelta(minutes=current_app.config['SESSION_IDLE_TIMEOUT_MINUTES'])
            if datetime.utcnow() - last_time > idle_timeout:
                current_app.logger.info("Session expired: idle timeout")
                return False
        
        return True
    
    def create_session(self) -> Dict[str, Any]:
        """Create a new secure session."""
        session.clear()
        
        # Generate secure token
        token = self.generate_secure_token()
        session[SESSION_TOKEN_KEY] = token
        
        # Generate and store fingerprint
        fingerprint = self.generate_enhanced_fingerprint()
        session[SESSION_FINGERPRINT_KEY] = fingerprint
        
        # Set timestamps
        now = datetime.utcnow().isoformat()
        session[SESSION_CREATED_KEY] = now
        session[SESSION_LAST_ACTIVITY_KEY] = now
        
        # Initialize rotation counter
        session[SESSION_ROTATION_COUNT_KEY] = 0
        
        # Set secure cookie flags
        self._set_secure_cookie_flags()
        
        current_app.logger.info(f"New session created: {token[:8]}...")
        
        return {
            'token': token,
            'fingerprint': fingerprint,
            'created': now
        }
    
    def update_session_activity(self):
        """Update last activity timestamp and rotate token if needed."""
        if SESSION_TOKEN_KEY not in session:
            return
        
        # Update last activity
        session[SESSION_LAST_ACTIVITY_KEY] = datetime.utcnow().isoformat()
        
        # Check if token rotation is needed
        rotation_count = session.get(SESSION_ROTATION_COUNT_KEY, 0)
        rotation_count += 1
        
        if rotation_count >= current_app.config['SESSION_ROTATION_INTERVAL']:
            self.rotate_session_token()
            rotation_count = 0
        
        session[SESSION_ROTATION_COUNT_KEY] = rotation_count
    
    def rotate_session_token(self):
        """Rotate the session token while preserving session data."""
        old_token = session.get(SESSION_TOKEN_KEY)
        new_token = self.generate_secure_token()
        session[SESSION_TOKEN_KEY] = new_token
        
        current_app.logger.info(
            f"Session token rotated: {old_token[:8]}... -> {new_token[:8]}..."
        )
    
    def _set_secure_cookie_flags(self):
        """Set secure cookie flags based on environment."""
        # These are set at the app config level
        pass
    
    def destroy_session(self):
        """Securely destroy the current session."""
        if SESSION_TOKEN_KEY in session:
            current_app.logger.info(
                f"Session destroyed: {session[SESSION_TOKEN_KEY][:8]}..."
            )
        session.clear()


# Create global instance
session_security = SessionSecurityManager()


# Middleware functions
def ensure_secure_session() -> None:
    """Ensure the session is secure and valid."""
    if not session_security.validate_session():
        # Invalid session, create new one
        session_security.create_session()
    else:
        # Valid session, update activity
        session_security.update_session_activity()


def require_valid_session():
    """Decorator to require a valid session for routes."""
    from functools import wraps
    
    def decorator(f):
        @wraps(f)
        async def decorated_function(*args, **kwargs):
            if not session_security.validate_session():
                raise Unauthorized("Invalid or expired session")
            return await f(*args, **kwargs)
        return decorated_function
    return decorator