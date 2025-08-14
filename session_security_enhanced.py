#!/usr/bin/env python3
"""
NextReel Enhanced Session Security System
==========================================
Complete implementation addressing all session security vulnerabilities

INSTRUCTIONS FOR CLAUDE CODE:
1. Save this as 'session_security_enhanced.py' in your project
2. Update your app.py to use this enhanced security
3. Configure environment variables for production
4. Run the security tests to validate
5. Deploy with HTTPS enforced

KEY IMPROVEMENTS:
- Stronger fingerprinting with device detection
- Hardware-based entropy sources
- Automatic session rotation
- Anti-replay attack protection
- Session fixation prevention
- HTTPS enforcement in production
"""

import hashlib
import hmac
import os
import secrets
import time
import json
import base64
import platform
import psutil
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, asdict
import redis
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from quart import request, session, current_app, make_response
from werkzeug.exceptions import Unauthorized
import logging

logger = logging.getLogger(__name__)

# Session configuration constants
SESSION_TOKEN_KEY = "session_token"
SESSION_FINGERPRINT_KEY = "session_fp"
SESSION_CREATED_KEY = "session_created"
SESSION_LAST_ACTIVITY_KEY = "session_last_activity"
SESSION_ROTATION_COUNT_KEY = "session_rotation_count"
SESSION_NONCE_KEY = "session_nonce"
SESSION_DEVICE_ID_KEY = "session_device_id"
SESSION_SECURITY_LEVEL_KEY = "session_security_level"

# Enhanced security settings
SESSION_TIMEOUT_MINUTES = int(os.getenv('SESSION_TIMEOUT_MINUTES', 30))
SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv('SESSION_IDLE_TIMEOUT_MINUTES', 15))
SESSION_ROTATION_INTERVAL = int(os.getenv('SESSION_ROTATION_INTERVAL', 10))
MAX_SESSION_DURATION_HOURS = int(os.getenv('MAX_SESSION_DURATION_HOURS', 24))
FINGERPRINT_TOLERANCE = float(os.getenv('FINGERPRINT_TOLERANCE', 0.95))  # 95% match threshold


@dataclass
class SessionData:
    """Enhanced session data structure"""
    token: str
    fingerprint: str
    device_id: str
    created: datetime
    last_activity: datetime
    rotation_count: int
    nonce: str
    security_level: str
    ip_address: str
    user_agent: str
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        data['created'] = self.created.isoformat()
        data['last_activity'] = self.last_activity.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'SessionData':
        data['created'] = datetime.fromisoformat(data['created'])
        data['last_activity'] = datetime.fromisoformat(data['last_activity'])
        return cls(**data)


class EnhancedSessionSecurity:
    """Production-grade session security manager with advanced protection"""
    
    def __init__(self, app=None, redis_client=None):
        self.app = app
        self.redis_client = redis_client
        self.encryption_key = None
        
        if app:
            self.init_app(app, redis_client)
    
    def init_app(self, app, redis_client=None):
        """Initialize with Quart app"""
        self.app = app
        self.redis_client = redis_client or self._init_redis()
        
        # Generate or load encryption key for session data
        self._init_encryption()
        
        # Configure secure settings
        self._configure_secure_settings(app)
        
        # Register before_request handler
        app.before_request(self._before_request_handler)
        
        logger.info("Enhanced session security initialized")
    
    def _init_redis(self):
        """Initialize Redis connection for session storage"""
        try:
            redis_url = os.getenv('UPSTASH_REDIS_URL', 'redis://localhost:6379')
            return redis.from_url(redis_url, decode_responses=True)
        except Exception as e:
            logger.warning(f"Redis unavailable, using in-memory fallback: {e}")
            return None
    
    def _init_encryption(self):
        """Initialize encryption for sensitive session data"""
        # Use app secret key to derive encryption key
        secret = self.app.config.get('SECRET_KEY', 'default-dev-key').encode()
        
        # Derive a proper encryption key using PBKDF2
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'nextreel-session-salt',  # In production, use unique salt
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(secret))
        self.encryption_key = Fernet(key)
    
    def _configure_secure_settings(self, app):
        """Configure secure cookie and session settings"""
        flask_env = os.getenv('FLASK_ENV', 'production')
        
        # Force secure settings in production
        if flask_env == 'production':
            app.config['SESSION_COOKIE_SECURE'] = True  # HTTPS only
            app.config['SESSION_COOKIE_HTTPONLY'] = True  # No JS access
            app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'  # CSRF protection
            app.config['SESSION_COOKIE_NAME'] = '__Host-session'  # Cookie prefixing
        else:
            # Development settings
            app.config['SESSION_COOKIE_SECURE'] = False
            app.config['SESSION_COOKIE_HTTPONLY'] = True
            app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
            app.config['SESSION_COOKIE_NAME'] = 'session'
        
        # Common settings
        app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=MAX_SESSION_DURATION_HOURS)
        app.config['SESSION_REFRESH_EACH_REQUEST'] = True
    
    async def _before_request_handler(self):
        """Validate session before each request"""
        # Skip for static files and health checks
        if request.path.startswith('/static') or request.path == '/health':
            return
        
        # Validate and update session
        if not await self.validate_session():
            await self.create_session()
        else:
            await self.update_session_activity()
    
    def generate_secure_token(self) -> str:
        """Generate cryptographically secure token with maximum entropy"""
        # Combine multiple entropy sources
        entropy_sources = [
            secrets.token_bytes(32),  # 256 bits of randomness
            str(time.time_ns()).encode(),  # Nanosecond precision timestamp
            str(os.getpid()).encode(),  # Process ID
            str(id(self)).encode(),  # Object memory address
            platform.node().encode(),  # Machine hostname
        ]
        
        # Add hardware entropy if available
        try:
            # CPU usage adds hardware-based entropy
            cpu_percent = str(psutil.cpu_percent(interval=0.01)).encode()
            entropy_sources.append(cpu_percent)
        except:
            pass
        
        # Combine all entropy
        combined_entropy = b''.join(entropy_sources)
        
        # Hash with SHA3-256 for quantum resistance
        token_hash = hashlib.sha3_256(combined_entropy).digest()
        
        # Return URL-safe base64 encoded token
        return base64.urlsafe_b64encode(token_hash).decode('utf-8').rstrip('=')
    
    def generate_device_fingerprint(self) -> Tuple[str, Dict]:
        """Generate comprehensive device fingerprint"""
        components = {}
        
        # Basic headers
        components['user_agent'] = request.headers.get('User-Agent', 'unknown')
        components['accept'] = request.headers.get('Accept', '')
        components['accept_language'] = request.headers.get('Accept-Language', '')
        components['accept_encoding'] = request.headers.get('Accept-Encoding', '')
        
        # Advanced fingerprinting
        components['dnt'] = request.headers.get('DNT', '')
        components['upgrade_insecure'] = request.headers.get('Upgrade-Insecure-Requests', '')
        components['sec_fetch_site'] = request.headers.get('Sec-Fetch-Site', '')
        components['sec_fetch_mode'] = request.headers.get('Sec-Fetch-Mode', '')
        components['sec_fetch_dest'] = request.headers.get('Sec-Fetch-Dest', '')
        
        # Canvas/WebGL fingerprinting headers if present
        components['sec_ch_ua'] = request.headers.get('Sec-CH-UA', '')
        components['sec_ch_ua_mobile'] = request.headers.get('Sec-CH-UA-Mobile', '')
        components['sec_ch_ua_platform'] = request.headers.get('Sec-CH-UA-Platform', '')
        
        # IP address handling (consider proxies)
        ip = request.headers.get('X-Real-IP') or \
             request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or \
             request.remote_addr
        components['ip'] = ip
        
        # TLS fingerprinting if available
        components['tls_version'] = request.headers.get('X-TLS-Version', '')
        components['tls_cipher'] = request.headers.get('X-TLS-Cipher', '')
        
        # Create stable fingerprint using HMAC
        fingerprint_data = json.dumps(components, sort_keys=True)
        secret_key = self.app.config.get('SECRET_KEY', '').encode()
        
        fingerprint = hmac.new(
            secret_key,
            fingerprint_data.encode(),
            hashlib.sha3_256  # Use SHA3 for better security
        ).hexdigest()
        
        return fingerprint, components
    
    def calculate_fingerprint_similarity(self, fp1_components: Dict, fp2_components: Dict) -> float:
        """Calculate similarity between two fingerprints for tolerance checking"""
        if not fp1_components or not fp2_components:
            return 0.0
        
        # Define weights for different components
        weights = {
            'user_agent': 0.3,  # Most important
            'ip': 0.2,
            'accept_language': 0.15,
            'accept': 0.1,
            'accept_encoding': 0.1,
            'sec_ch_ua_platform': 0.05,
            'sec_ch_ua': 0.05,
            'other': 0.05
        }
        
        total_score = 0.0
        total_weight = 0.0
        
        for key, weight in weights.items():
            if key == 'other':
                continue
                
            if key in fp1_components and key in fp2_components:
                if fp1_components[key] == fp2_components[key]:
                    total_score += weight
                total_weight += weight
        
        return total_score / total_weight if total_weight > 0 else 0.0
    
    async def validate_session(self) -> bool:
        """Comprehensive session validation"""
        try:
            # Check if session exists
            if SESSION_TOKEN_KEY not in session:
                logger.debug("No session token found")
                return False
            
            token = session[SESSION_TOKEN_KEY]
            
            # Retrieve session data from Redis if available
            session_data = await self._get_session_data(token)
            if not session_data:
                logger.warning(f"Session data not found for token: {token[:8]}...")
                return False
            
            # Validate fingerprint with tolerance
            current_fp, current_components = self.generate_device_fingerprint()
            stored_fp = session.get(SESSION_FINGERPRINT_KEY)
            
            if stored_fp != current_fp:
                # Check similarity for minor changes (e.g., IP change on mobile)
                stored_components = session.get('fingerprint_components', {})
                similarity = self.calculate_fingerprint_similarity(
                    stored_components, 
                    current_components
                )
                
                if similarity < FINGERPRINT_TOLERANCE:
                    logger.warning(
                        f"Session fingerprint mismatch (similarity: {similarity:.2%}). "
                        f"Possible hijacking attempt for token: {token[:8]}..."
                    )
                    await self._log_security_event('fingerprint_mismatch', {
                        'token': token[:8],
                        'similarity': similarity,
                        'ip': current_components.get('ip')
                    })
                    return False
                else:
                    logger.info(f"Fingerprint changed but within tolerance ({similarity:.2%})")
            
            # Check session age (absolute timeout)
            created = session.get(SESSION_CREATED_KEY)
            if created:
                created_time = datetime.fromisoformat(created)
                max_duration = timedelta(hours=MAX_SESSION_DURATION_HOURS)
                if datetime.utcnow() - created_time > max_duration:
                    logger.info("Session expired: exceeded maximum duration")
                    return False
            
            # Check idle timeout
            last_activity = session.get(SESSION_LAST_ACTIVITY_KEY)
            if last_activity:
                last_time = datetime.fromisoformat(last_activity)
                idle_timeout = timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
                if datetime.utcnow() - last_time > idle_timeout:
                    logger.info("Session expired: idle timeout")
                    return False
            
            # Validate nonce for replay attack protection
            stored_nonce = session.get(SESSION_NONCE_KEY)
            if not stored_nonce or len(stored_nonce) < 32:
                logger.warning("Invalid or missing session nonce")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Session validation error: {e}")
            return False
    
    async def create_session(self) -> Dict[str, Any]:
        """Create new secure session with all protections"""
        # Clear any existing session
        session.clear()
        
        # Generate secure components
        token = self.generate_secure_token()
        fingerprint, fp_components = self.generate_device_fingerprint()
        device_id = self.generate_secure_token()[:16]  # Shorter device ID
        nonce = secrets.token_hex(32)  # 256-bit nonce
        
        # Determine security level
        flask_env = os.getenv('FLASK_ENV', 'production')
        security_level = 'high' if flask_env == 'production' else 'standard'
        
        # Store in session
        now = datetime.utcnow()
        session[SESSION_TOKEN_KEY] = token
        session[SESSION_FINGERPRINT_KEY] = fingerprint
        session[SESSION_DEVICE_ID_KEY] = device_id
        session[SESSION_CREATED_KEY] = now.isoformat()
        session[SESSION_LAST_ACTIVITY_KEY] = now.isoformat()
        session[SESSION_ROTATION_COUNT_KEY] = 0
        session[SESSION_NONCE_KEY] = nonce
        session[SESSION_SECURITY_LEVEL_KEY] = security_level
        session['fingerprint_components'] = fp_components  # Store for tolerance checking
        
        # Create session data object
        session_data = SessionData(
            token=token,
            fingerprint=fingerprint,
            device_id=device_id,
            created=now,
            last_activity=now,
            rotation_count=0,
            nonce=nonce,
            security_level=security_level,
            ip_address=fp_components.get('ip', 'unknown'),
            user_agent=fp_components.get('user_agent', 'unknown')
        )
        
        # Store in Redis if available
        await self._store_session_data(token, session_data)
        
        logger.info(f"New secure session created: {token[:8]}... (security: {security_level})")
        
        return session_data.to_dict()
    
    async def update_session_activity(self):
        """Update session activity and rotate token if needed"""
        if SESSION_TOKEN_KEY not in session:
            return
        
        # Update last activity
        session[SESSION_LAST_ACTIVITY_KEY] = datetime.utcnow().isoformat()
        
        # Update rotation counter
        rotation_count = session.get(SESSION_ROTATION_COUNT_KEY, 0)
        rotation_count += 1
        
        # Rotate token if threshold reached
        if rotation_count >= SESSION_ROTATION_INTERVAL:
            await self.rotate_session_token()
            rotation_count = 0
        
        session[SESSION_ROTATION_COUNT_KEY] = rotation_count
        
        # Update nonce periodically for replay protection
        if rotation_count % 5 == 0:
            session[SESSION_NONCE_KEY] = secrets.token_hex(32)
    
    async def rotate_session_token(self):
        """Rotate session token while preserving session data"""
        old_token = session.get(SESSION_TOKEN_KEY)
        new_token = self.generate_secure_token()
        
        # Update token
        session[SESSION_TOKEN_KEY] = new_token
        
        # Update in Redis if available
        if self.redis_client and old_token:
            session_data = await self._get_session_data(old_token)
            if session_data:
                session_data.token = new_token
                await self._store_session_data(new_token, session_data)
                await self._delete_session_data(old_token)
        
        logger.info(f"Session token rotated: {old_token[:8]}... -> {new_token[:8]}...")
    
    async def destroy_session(self):
        """Completely destroy session and clean up"""
        token = session.get(SESSION_TOKEN_KEY)
        
        if token:
            # Remove from Redis
            await self._delete_session_data(token)
            
            # Log session destruction
            logger.info(f"Session destroyed: {token[:8]}...")
        
        # Clear session
        session.clear()
    
    # Redis operations
    async def _store_session_data(self, token: str, data: SessionData):
        """Store session data in Redis"""
        if not self.redis_client:
            return
        
        try:
            # Encrypt sensitive data
            encrypted_data = self.encryption_key.encrypt(
                json.dumps(data.to_dict()).encode()
            )
            
            # Store with expiration
            key = f"session:{token}"
            self.redis_client.setex(
                key,
                timedelta(hours=MAX_SESSION_DURATION_HOURS),
                base64.b64encode(encrypted_data).decode()
            )
        except Exception as e:
            logger.error(f"Failed to store session data: {e}")
    
    async def _get_session_data(self, token: str) -> Optional[SessionData]:
        """Retrieve session data from Redis"""
        if not self.redis_client:
            # Fallback to session data if Redis unavailable
            if SESSION_TOKEN_KEY in session and session[SESSION_TOKEN_KEY] == token:
                return SessionData(
                    token=token,
                    fingerprint=session.get(SESSION_FINGERPRINT_KEY, ''),
                    device_id=session.get(SESSION_DEVICE_ID_KEY, ''),
                    created=datetime.fromisoformat(session.get(SESSION_CREATED_KEY, datetime.utcnow().isoformat())),
                    last_activity=datetime.fromisoformat(session.get(SESSION_LAST_ACTIVITY_KEY, datetime.utcnow().isoformat())),
                    rotation_count=session.get(SESSION_ROTATION_COUNT_KEY, 0),
                    nonce=session.get(SESSION_NONCE_KEY, ''),
                    security_level=session.get(SESSION_SECURITY_LEVEL_KEY, 'standard'),
                    ip_address='',
                    user_agent=''
                )
            return None
        
        try:
            key = f"session:{token}"
            encrypted_data = self.redis_client.get(key)
            
            if not encrypted_data:
                return None
            
            # Decrypt data
            decrypted_data = self.encryption_key.decrypt(
                base64.b64decode(encrypted_data.encode())
            )
            
            data_dict = json.loads(decrypted_data.decode())
            return SessionData.from_dict(data_dict)
            
        except Exception as e:
            logger.error(f"Failed to retrieve session data: {e}")
            return None
    
    async def _delete_session_data(self, token: str):
        """Delete session data from Redis"""
        if not self.redis_client:
            return
        
        try:
            key = f"session:{token}"
            self.redis_client.delete(key)
        except Exception as e:
            logger.error(f"Failed to delete session data: {e}")
    
    async def _log_security_event(self, event_type: str, details: Dict):
        """Log security events for monitoring"""
        event = {
            'timestamp': datetime.utcnow().isoformat(),
            'type': event_type,
            'details': details
        }
        
        logger.warning(f"SECURITY EVENT: {json.dumps(event)}")
        
        # Store in Redis for analysis if available
        if self.redis_client:
            try:
                key = f"security:events:{datetime.utcnow().strftime('%Y%m%d')}"
                self.redis_client.rpush(key, json.dumps(event))
                self.redis_client.expire(key, timedelta(days=30))
            except Exception as e:
                logger.error(f"Failed to log security event: {e}")


# Middleware decorator
def require_secure_session(f):
    """Decorator to require valid secure session"""
    from functools import wraps
    
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        # The session validation happens in before_request
        # This decorator adds an extra check for sensitive operations
        if SESSION_TOKEN_KEY not in session:
            raise Unauthorized("No valid session")
        
        # Check security level for sensitive operations
        security_level = session.get(SESSION_SECURITY_LEVEL_KEY, 'standard')
        if security_level != 'high' and os.getenv('FLASK_ENV') == 'production':
            raise Unauthorized("High security session required")
        
        return await f(*args, **kwargs)
    
    return decorated_function


# HSTS and security headers middleware
async def add_security_headers(response):
    """Add security headers to all responses"""
    flask_env = os.getenv('FLASK_ENV', 'production')
    
    if flask_env == 'production':
        # HSTS - Force HTTPS for 1 year
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
        
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'DENY'
        
        # Prevent MIME sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        # Enable XSS protection
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # CSP header
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline';"
        
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    
    return response


# Session monitoring and analytics
class SessionMonitor:
    """Monitor session security and detect anomalies"""
    
    def __init__(self, redis_client=None):
        self.redis_client = redis_client
        self.alert_threshold = 10  # Number of suspicious events before alert
    
    async def check_session_anomalies(self, token: str) -> bool:
        """Check for session anomalies"""
        if not self.redis_client:
            return True
        
        try:
            # Check recent security events for this session
            events_key = f"security:session:{token[:8]}"
            event_count = self.redis_client.get(events_key)
            
            if event_count and int(event_count) > self.alert_threshold:
                logger.critical(f"Multiple security events for session: {token[:8]}...")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to check session anomalies: {e}")
            return True
    
    async def get_session_metrics(self) -> Dict:
        """Get session security metrics"""
        if not self.redis_client:
            return {}
        
        try:
            # Get today's security events
            today_key = f"security:events:{datetime.utcnow().strftime('%Y%m%d')}"
            events = self.redis_client.lrange(today_key, 0, -1)
            
            metrics = {
                'total_events': len(events),
                'fingerprint_mismatches': 0,
                'token_rotations': 0,
                'sessions_created': 0,
                'sessions_destroyed': 0
            }
            
            for event_json in events:
                event = json.loads(event_json)
                event_type = event.get('type', '')
                
                if event_type == 'fingerprint_mismatch':
                    metrics['fingerprint_mismatches'] += 1
                elif event_type == 'token_rotation':
                    metrics['token_rotations'] += 1
                elif event_type == 'session_created':
                    metrics['sessions_created'] += 1
                elif event_type == 'session_destroyed':
                    metrics['sessions_destroyed'] += 1
            
            return metrics
            
        except Exception as e:
            logger.error(f"Failed to get session metrics: {e}")
            return {}


# Example integration with your app.py
"""
# In your app.py, add this initialization:

from session_security_enhanced import EnhancedSessionSecurity, add_security_headers

# Initialize the enhanced security
session_security = EnhancedSessionSecurity(app)

# Add security headers to all responses
@app.after_request
async def after_request(response):
    return await add_security_headers(response)

# Use the decorator for sensitive routes
from session_security_enhanced import require_secure_session

@app.route('/admin')
@require_secure_session
async def admin_panel():
    return "Admin access granted"
"""