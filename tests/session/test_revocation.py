import json
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def fake_redis():
    return AsyncMock()


async def test_revokes_sessions_for_matching_user(fake_redis):
    from session.revocation import revoke_user_sessions

    keys_batch = [b"quart-session:sid-a", b"quart-session:sid-b", b"quart-session:sid-c"]
    fake_redis.scan.side_effect = [(0, keys_batch)]
    # get() is only called for keys NOT equal to the except_suffix, so sid-a
    # is skipped and only sid-b + sid-c payloads are consumed.
    fake_redis.get.side_effect = [
        json.dumps({"user_id": "u2"}).encode(),
        json.dumps({"user_id": "u1"}).encode(),
    ]

    count = await revoke_user_sessions(fake_redis, "u1", except_session_id="sid-a")

    assert count == 1
    deleted_keys = [call.args[0] for call in fake_redis.delete.await_args_list]
    assert b"quart-session:sid-c" in deleted_keys
    assert b"quart-session:sid-a" not in deleted_keys
    assert b"quart-session:sid-b" not in deleted_keys


async def test_revokes_all_when_except_is_none(fake_redis):
    from session.revocation import revoke_user_sessions

    fake_redis.scan.side_effect = [(0, [b"quart-session:sid-a", b"quart-session:sid-b"])]
    fake_redis.get.side_effect = [
        json.dumps({"user_id": "u1"}).encode(),
        json.dumps({"user_id": "u1"}).encode(),
    ]

    count = await revoke_user_sessions(fake_redis, "u1", except_session_id=None)

    assert count == 2
    assert fake_redis.delete.await_count == 2


async def test_skips_sessions_for_other_users(fake_redis):
    from session.revocation import revoke_user_sessions

    fake_redis.scan.side_effect = [(0, [b"quart-session:sid-x"])]
    fake_redis.get.side_effect = [json.dumps({"user_id": "other"}).encode()]

    count = await revoke_user_sessions(fake_redis, "u1", except_session_id=None)

    assert count == 0
    fake_redis.delete.assert_not_awaited()


async def test_tolerates_unparseable_session_values(fake_redis):
    from session.revocation import revoke_user_sessions

    fake_redis.scan.side_effect = [(0, [b"quart-session:bad"])]
    fake_redis.get.side_effect = [b"not-json-at-all"]

    count = await revoke_user_sessions(fake_redis, "u1", except_session_id=None)

    assert count == 0
