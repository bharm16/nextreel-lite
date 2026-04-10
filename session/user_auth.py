"""User registration, login, and OAuth account management."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pymysql
from email_validator import EmailNotValidError, validate_email

from infra.time_utils import utcnow
from logging_config import get_logger

logger = get_logger(__name__)

MIN_PASSWORD_LENGTH = 8
EMAIL_PASSWORD_AUTH_UNAVAILABLE_MESSAGE = (
    "Email/password sign-in is currently unavailable. Please try again later."
)

# MySQL error code for duplicate-entry on a UNIQUE key. Tested against the
# pymysql/aiomysql IntegrityError raised when two concurrent register
# requests race past the pre-insert duplicate-email check and both try to
# INSERT the same email. The users table has UNIQUE KEY idx_users_email.
_MYSQL_DUP_ENTRY_ERRNO = 1062


class DuplicateUserError(Exception):
    """Raised when a user already exists for the given email.

    The register route catches this to render the "account already exists"
    error regardless of whether the duplicate was caught by the pre-check
    or by the database unique constraint (TOCTOU race window).
    """


class EmailPasswordAuthUnavailableError(RuntimeError):
    """Raised when email/password auth dependencies are unavailable."""


def _load_bcrypt():
    try:
        import bcrypt
    except ModuleNotFoundError as exc:
        if exc.name == "bcrypt":
            raise EmailPasswordAuthUnavailableError(
                "bcrypt is required for email/password authentication"
            ) from exc
        raise
    return bcrypt


async def hash_password_async(password: str) -> str:
    """Bcrypt hash a password off the event loop.

    bcrypt is CPU-bound (hundreds of ms); run it in a worker thread so we
    don't block the Quart event loop on auth requests.
    """
    bcrypt = _load_bcrypt()
    return await asyncio.to_thread(
        lambda: bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    )


async def verify_password_async(password: str, password_hash: str) -> bool:
    """Bcrypt-verify a password off the event loop."""
    bcrypt = _load_bcrypt()
    return await asyncio.to_thread(
        bcrypt.checkpw,
        password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )


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
    *,
    precomputed_hash: str | None = None,
) -> str:
    """Create a new email+password user. Returns user_id.

    ``precomputed_hash`` is a keyword-only escape hatch for callers that
    already hashed the password concurrently with a duplicate-email check.
    It is intentionally keyword-only so positional-arg drift cannot slip
    an unhashed password in.
    """
    user_id = uuid4().hex
    if precomputed_hash is not None:
        password_hash = precomputed_hash
    else:
        password_hash = await hash_password_async(password)
    now = utcnow()

    try:
        await db_pool.execute(
            """
            INSERT INTO users (user_id, email, password_hash, display_name,
                               auth_provider, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [user_id, email.lower().strip(), password_hash, display_name, "email", now, now],
            fetch="none",
        )
    except pymysql.err.IntegrityError as exc:
        # errno 1062 = duplicate entry on UNIQUE key. Map to a domain
        # exception so the register route can render the right error even
        # when the duplicate slipped past the pre-insert email check.
        errno = exc.args[0] if exc.args else None
        if errno == _MYSQL_DUP_ENTRY_ERRNO:
            logger.info(
                "Duplicate-email race for %s caught by UNIQUE constraint",
                email,
            )
            raise DuplicateUserError(email) from exc
        raise
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

    if await verify_password_async(password, row["password_hash"]):
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
