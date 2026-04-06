"""User registration, login, and OAuth account management."""

from __future__ import annotations

from uuid import uuid4

import bcrypt
from email_validator import EmailNotValidError, validate_email

from infra.time_utils import utcnow
from logging_config import get_logger

logger = get_logger(__name__)

MIN_PASSWORD_LENGTH = 8


def validate_registration(email: str, password: str, confirm_password: str) -> dict[str, str]:
    """Validate registration form inputs. Returns {field: error_message} dict."""
    errors: dict[str, str] = {}

    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        errors["email"] = "Please enter a valid email address."

    if len(password) < MIN_PASSWORD_LENGTH:
        errors["password"] = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."

    if password != confirm_password:
        errors["confirm_password"] = "Passwords do not match."

    return errors


async def register_user(
    db_pool,
    email: str,
    password: str,
    display_name: str | None = None,
) -> str:
    """Create a new email+password user. Returns user_id."""
    user_id = uuid4().hex
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    now = utcnow()

    await db_pool.execute(
        """
        INSERT INTO users (user_id, email, password_hash, display_name,
                           auth_provider, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        [user_id, email.lower().strip(), password_hash, display_name, "email", now, now],
        fetch="none",
    )
    logger.info("Registered user %s via email", user_id)
    return user_id


async def authenticate_user(db_pool, email: str, password: str) -> str | None:
    """Verify email+password credentials. Returns user_id or None."""
    row = await db_pool.execute(
        """
        SELECT user_id, password_hash
        FROM users
        WHERE email = %s AND auth_provider = 'email'
        """,
        [email.lower().strip()],
        fetch="one",
    )
    if not row or not row.get("password_hash"):
        return None

    if bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        return row["user_id"]
    return None


async def find_or_create_oauth_user(
    db_pool,
    *,
    provider: str,
    oauth_sub: str,
    email: str,
    display_name: str | None = None,
) -> str:
    """Find an existing OAuth user or create a new one. Returns user_id."""
    row = await db_pool.execute(
        """
        SELECT user_id FROM users
        WHERE auth_provider = %s AND oauth_sub = %s
        """,
        [provider, oauth_sub],
        fetch="one",
    )
    if row:
        return row["user_id"]

    user_id = uuid4().hex
    now = utcnow()
    await db_pool.execute(
        """
        INSERT INTO users (user_id, email, password_hash, display_name,
                           auth_provider, oauth_sub, created_at, updated_at)
        VALUES (%s, %s, NULL, %s, %s, %s, %s, %s)
        """,
        [user_id, email.lower().strip(), display_name, provider, oauth_sub, now, now],
        fetch="none",
    )
    logger.info("Created OAuth user %s via %s", user_id, provider)
    return user_id


async def get_user_by_id(db_pool, user_id: str) -> dict | None:
    """Fetch user record by user_id."""
    return await db_pool.execute(
        "SELECT user_id, email, display_name, auth_provider, created_at "
        "FROM users WHERE user_id = %s",
        [user_id],
        fetch="one",
    )


async def get_user_by_email(db_pool, email: str) -> dict | None:
    """Fetch user record by email."""
    return await db_pool.execute(
        "SELECT user_id, email, display_name, auth_provider FROM users WHERE email = %s",
        [email.lower().strip()],
        fetch="one",
    )
