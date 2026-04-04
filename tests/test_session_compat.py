from unittest.mock import AsyncMock

import pytest
from quart import Quart, Response

from session.quart_session_compat import CompatibleRedisSessionInterface


@pytest.mark.asyncio
async def test_compatible_redis_session_interface_decodes_signed_cookie_value():
    app = Quart(__name__)
    app.secret_key = "test-secret"
    app.config["SESSION_COOKIE_NAME"] = "session"
    app.config["SESSION_USE_SIGNER"] = True

    interface = CompatibleRedisSessionInterface(
        redis=None,
        key_prefix="session:",
        use_signer=True,
        permanent=False,
        SESSION_STATIC_FILE=False,
        SESSION_KEY_PREFIX="session:",
        SESSION_USE_SIGNER=True,
        SESSION_PERMANENT=False,
        SESSION_PROTECTION=False,
    )
    interface.set = AsyncMock()
    session = interface.session_class({"user_id": "123"}, sid="abc123", permanent=False)
    session.modified = True
    response = Response("ok")

    await interface.save_session(app, session, response)

    interface.set.assert_awaited_once()
    set_cookie = response.headers["Set-Cookie"]
    assert "session=" in set_cookie
    assert "abc123." in set_cookie
