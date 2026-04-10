"""Tests for session/user_auth.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from session.user_auth import (
    MIN_PASSWORD_LENGTH,
    DuplicateUserError,
    authenticate_user,
    find_or_create_oauth_user,
    get_user_by_email,
    get_user_by_id,
    register_user,
    validate_registration,
)


# ---------------------------------------------------------------------------
# validate_registration
# ---------------------------------------------------------------------------


def test_validate_registration_valid():
    errors = validate_registration("user@example.com", "securepass", "securepass")
    assert errors == {}


def test_validate_registration_invalid_email():
    errors = validate_registration("not-an-email", "securepass", "securepass")
    assert "email" in errors
    assert "valid email" in errors["email"]


def test_validate_registration_short_password():
    short = "x" * (MIN_PASSWORD_LENGTH - 1)
    errors = validate_registration("user@example.com", short, short)
    assert "password" in errors
    assert str(MIN_PASSWORD_LENGTH) in errors["password"]


def test_validate_registration_password_mismatch():
    errors = validate_registration("user@example.com", "securepass", "different")
    assert "confirm_password" in errors
    assert "do not match" in errors["confirm_password"]


def test_validate_registration_multiple_errors():
    errors = validate_registration("bad", "x", "y")
    assert "email" in errors
    assert "password" in errors
    assert "confirm_password" in errors


# ---------------------------------------------------------------------------
# register_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_user_returns_user_id(mock_db_pool):
    mock_db_pool.execute.return_value = None

    user_id = await register_user(mock_db_pool, "User@Example.com", "mypassword123")

    assert isinstance(user_id, str)
    assert len(user_id) == 32  # uuid4().hex is 32 chars

    call_args = mock_db_pool.execute.call_args
    params = call_args[0][1]
    assert params[1] == "user@example.com"  # email lowercased + stripped
    assert params[4] == "email"  # auth_provider
    assert call_args[1]["fetch"] == "none"


@pytest.mark.asyncio
async def test_register_user_hashes_password(mock_db_pool):
    mock_db_pool.execute.return_value = None

    with patch(
        "session.user_auth.hash_password_async",
        AsyncMock(return_value="hashed-plainpassword"),
    ) as hash_password:
        await register_user(mock_db_pool, "user@example.com", "plainpassword")

    hash_password.assert_awaited_once_with("plainpassword")

    call_args = mock_db_pool.execute.call_args
    stored_hash = call_args[0][1][2]  # password_hash param
    assert stored_hash == "hashed-plainpassword"


@pytest.mark.asyncio
async def test_register_user_with_display_name(mock_db_pool):
    mock_db_pool.execute.return_value = None

    await register_user(mock_db_pool, "user@example.com", "password123", display_name="Alice")

    call_args = mock_db_pool.execute.call_args
    params = call_args[0][1]
    assert params[3] == "Alice"  # display_name param


# ---------------------------------------------------------------------------
# authenticate_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_user_valid_credentials(mock_db_pool):
    password = "correctpassword"
    mock_db_pool.execute.return_value = {
        "user_id": "abc123",
        "password_hash": "stored-hash",
    }

    with patch(
        "session.user_auth.verify_password_async",
        AsyncMock(return_value=True),
    ) as verify_password:
        result = await authenticate_user(mock_db_pool, "user@example.com", password)

    verify_password.assert_awaited_once_with(password, "stored-hash")
    assert result == "abc123"


@pytest.mark.asyncio
async def test_authenticate_user_wrong_password(mock_db_pool):
    mock_db_pool.execute.return_value = {
        "user_id": "abc123",
        "password_hash": "stored-hash",
    }

    with patch(
        "session.user_auth.verify_password_async",
        AsyncMock(return_value=False),
    ) as verify_password:
        result = await authenticate_user(mock_db_pool, "user@example.com", "wrongpassword")

    verify_password.assert_awaited_once_with("wrongpassword", "stored-hash")
    assert result is None


@pytest.mark.asyncio
async def test_authenticate_user_not_found(mock_db_pool):
    mock_db_pool.execute.return_value = None

    result = await authenticate_user(mock_db_pool, "nobody@example.com", "anypassword")

    assert result is None


@pytest.mark.asyncio
async def test_authenticate_user_no_password_hash(mock_db_pool):
    mock_db_pool.execute.return_value = {"user_id": "abc123", "password_hash": None}

    result = await authenticate_user(mock_db_pool, "user@example.com", "anypassword")

    assert result is None


@pytest.mark.asyncio
async def test_authenticate_user_normalizes_email(mock_db_pool):
    mock_db_pool.execute.return_value = None

    await authenticate_user(mock_db_pool, "  USER@Example.COM  ", "pass")

    call_args = mock_db_pool.execute.call_args
    params = call_args[0][1]
    assert params[0] == "user@example.com"


# ---------------------------------------------------------------------------
# find_or_create_oauth_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_or_create_oauth_user_existing(mock_db_pool):
    mock_db_pool.execute.return_value = {"user_id": "existing_user_id"}

    result = await find_or_create_oauth_user(
        mock_db_pool,
        provider="google",
        oauth_sub="google-sub-123",
        email="user@example.com",
    )

    assert result == "existing_user_id"
    # Should only call execute once (the SELECT)
    assert mock_db_pool.execute.call_count == 1


@pytest.mark.asyncio
async def test_find_or_create_oauth_user_new(mock_db_pool):
    # First call (SELECT) returns None, second call (INSERT) returns None
    mock_db_pool.execute.side_effect = [None, None]

    result = await find_or_create_oauth_user(
        mock_db_pool,
        provider="github",
        oauth_sub="github-sub-456",
        email="newuser@example.com",
        display_name="New User",
    )

    assert isinstance(result, str)
    assert len(result) == 32

    # Two DB calls: SELECT then INSERT
    assert mock_db_pool.execute.call_count == 2

    insert_call = mock_db_pool.execute.call_args_list[1]
    params = insert_call[0][1]
    assert params[1] == "newuser@example.com"
    assert params[2] == "New User"  # display_name
    assert params[3] == "github"  # provider
    assert params[4] == "github-sub-456"  # oauth_sub
    assert insert_call[1]["fetch"] == "none"


@pytest.mark.asyncio
async def test_find_or_create_oauth_user_new_normalizes_email(mock_db_pool):
    mock_db_pool.execute.side_effect = [None, None]

    await find_or_create_oauth_user(
        mock_db_pool,
        provider="google",
        oauth_sub="sub-789",
        email="  UPPER@Example.COM  ",
    )

    insert_call = mock_db_pool.execute.call_args_list[1]
    params = insert_call[0][1]
    assert params[1] == "upper@example.com"


# ---------------------------------------------------------------------------
# get_user_by_id / get_user_by_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_by_id(mock_db_pool):
    expected = {
        "user_id": "abc",
        "email": "u@example.com",
        "display_name": None,
        "auth_provider": "email",
        "created_at": None,
    }
    mock_db_pool.execute.return_value = expected

    result = await get_user_by_id(mock_db_pool, "abc")

    assert result == expected
    call_args = mock_db_pool.execute.call_args
    assert call_args[1]["fetch"] == "one"
    assert call_args[0][1] == ["abc"]


@pytest.mark.asyncio
async def test_get_user_by_id_not_found(mock_db_pool):
    mock_db_pool.execute.return_value = None

    result = await get_user_by_id(mock_db_pool, "nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_get_user_by_email(mock_db_pool):
    expected = {
        "user_id": "abc",
        "email": "u@example.com",
        "display_name": None,
        "auth_provider": "email",
    }
    mock_db_pool.execute.return_value = expected

    result = await get_user_by_email(mock_db_pool, "U@Example.COM")

    assert result == expected
    call_args = mock_db_pool.execute.call_args
    assert call_args[1]["fetch"] == "one"
    assert call_args[0][1] == ["u@example.com"]


@pytest.mark.asyncio
async def test_get_user_by_email_not_found(mock_db_pool):
    mock_db_pool.execute.return_value = None

    result = await get_user_by_email(mock_db_pool, "nobody@example.com")

    assert result is None


# ---------------------------------------------------------------------------
# DuplicateUserError on concurrent race past pre-check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_user_raises_duplicate_on_unique_violation(mock_db_pool):
    """C2 regression: if two concurrent register requests race past the
    pre-insert duplicate-email check, the second INSERT must raise
    DuplicateUserError (mapped from pymysql errno 1062) so the route can
    render the same error as the pre-check branch.
    """
    import pymysql

    mock_db_pool.execute = AsyncMock(
        side_effect=pymysql.err.IntegrityError(
            1062, "Duplicate entry 'dup@example.com' for key 'idx_users_email'"
        )
    )

    with pytest.raises(DuplicateUserError):
        await register_user(
            mock_db_pool,
            "dup@example.com",
            "password123",
            None,
            precomputed_hash="$2b$12$precomputed",
        )


@pytest.mark.asyncio
async def test_register_user_reraises_other_integrity_errors(mock_db_pool):
    """Non-1062 IntegrityErrors (e.g. FK violations) must propagate
    unchanged — we only bucket duplicate-entry as DuplicateUserError.
    """
    import pymysql

    mock_db_pool.execute = AsyncMock(
        side_effect=pymysql.err.IntegrityError(
            1452, "Cannot add or update a child row: a foreign key constraint fails"
        )
    )

    with pytest.raises(pymysql.err.IntegrityError):
        await register_user(
            mock_db_pool,
            "x@example.com",
            "password123",
            None,
            precomputed_hash="$2b$12$precomputed",
        )
