import hashlib
import os
import secrets
from quart import request, session

SESSION_TOKEN_KEY = "session_token"
SESSION_FINGERPRINT_KEY = "session_fp"


def generate_session_token() -> str:
    """Generate a secure random session token."""
    return secrets.token_urlsafe(32)


def generate_fingerprint(user_agent: str, ip: str) -> str:
    """Generate a fingerprint tied to the user agent and IP."""
    data = f"{user_agent}|{ip}|{os.getenv('FLASK_SECRET_KEY', '')}"
    return hashlib.sha256(data.encode()).hexdigest()


def ensure_session() -> None:
    """Ensure the session contains a token and fingerprint bound to the client."""
    current_fp = generate_fingerprint(
        request.headers.get("User-Agent", ""), request.remote_addr or ""
    )
    token = session.get(SESSION_TOKEN_KEY)
    fp = session.get(SESSION_FINGERPRINT_KEY)

    if not token or fp != current_fp:
        session.clear()
        session[SESSION_TOKEN_KEY] = generate_session_token()
        session[SESSION_FINGERPRINT_KEY] = current_fp
