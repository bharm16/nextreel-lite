import hashlib
import os
import secrets
from datetime import datetime, timedelta
import logging
from quart import request, session

SESSION_TOKEN_KEY = "session_token"
SESSION_FINGERPRINT_KEY = "session_fp"
TOKEN_TIMESTAMP_KEY = "token_created_at"

def generate_session_token() -> str:
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)

def generate_fingerprint(user_agent: str, accept_language: str, accept_encoding: str) -> str:
    """Generate a fingerprint from key request headers."""
    data = f"{user_agent}|{accept_language}|{accept_encoding}|{os.getenv('FLASK_SECRET_KEY', '')}"
    return hashlib.sha256(data.encode()).hexdigest()

def ensure_session() -> None:
    """Ensure the session contains a rotated token and fingerprint."""
    headers = request.headers
    current_fp = generate_fingerprint(
        headers.get("User-Agent", ""),
        headers.get("Accept-Language", ""),
        headers.get("Accept-Encoding", ""),
    )
    token = session.get(SESSION_TOKEN_KEY)
    fp = session.get(SESSION_FINGERPRINT_KEY)
    if fp and fp != current_fp:
        logging.warning("Session fingerprint changed; possible hijacking")
    session[SESSION_FINGERPRINT_KEY] = current_fp
    session["device_fingerprint"] = current_fp

    created_str = session.get(TOKEN_TIMESTAMP_KEY)
    created_at = datetime.fromisoformat(created_str) if created_str else None
    if not token or not created_at or datetime.utcnow() - created_at > timedelta(days=7):
        session[SESSION_TOKEN_KEY] = generate_session_token()
        session[TOKEN_TIMESTAMP_KEY] = datetime.utcnow().isoformat()
