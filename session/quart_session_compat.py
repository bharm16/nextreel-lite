"""Compatibility helpers for quart-session.

quart-session 3.0.0 signs Redis-backed session IDs as bytes in
``save_session`` and passes them directly to Werkzeug's ``set_cookie``.
Recent Werkzeug versions require cookie values to be strings, which turns
otherwise-valid requests into 500s during response finalization.
"""

from __future__ import annotations

from quart_session import Session
from quart_session.sessions import FileBody, RedisSessionInterface, want_bytes

from logging_config import get_logger

logger = get_logger(__name__)


class CompatibleRedisSessionInterface(RedisSessionInterface):
    """Redis session interface that normalizes signed cookie values to text."""

    async def save_session(self, app, session, response) -> None:  # type: ignore[override]
        if not session.modified:
            return

        config = getattr(self, "_config", None)
        static_file = config.get("SESSION_STATIC_FILE") if config is not None else False
        if static_file is False and isinstance(response.response, FileBody):
            return

        cname = app.config.get("SESSION_COOKIE_NAME", "session")
        session_key = self.key_prefix + session.sid
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)

        if not session:
            if session.modified:
                await self.delete(key=session_key, app=app)
                response.delete_cookie(cname, domain=domain, path=path)
            return

        httponly = self.get_cookie_httponly(app)
        samesite = self.get_cookie_samesite(app)
        secure = self.get_cookie_secure(app)
        expires = self.get_expiration_time(app, session)

        if self.serializer is None:
            val = dict(session)
        else:
            val = self.serializer.dumps(dict(session))

        await self.set(key=session_key, value=val, app=app)
        if self.use_signer:
            session_id = self._get_signer(app).sign(want_bytes(session.sid))
            if isinstance(session_id, bytes):
                session_id = session_id.decode("utf-8")
        else:
            session_id = session.sid

        response.set_cookie(
            cname,
            session_id,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            samesite=samesite,
        )


def install_session(app) -> None:
    """Install quart-session and replace the Redis backend with the compat wrapper."""
    Session(app)

    session_interface = app.session_interface
    if isinstance(session_interface, RedisSessionInterface):
        config = getattr(session_interface, "_config", None)
        if config is None:
            logger.warning(
                "quart-session %s lacks _config attribute; compat shim disabled",
                type(session_interface).__name__,
            )
            return
        app.session_interface = CompatibleRedisSessionInterface(
            redis=session_interface.backend,
            key_prefix=session_interface.key_prefix,
            use_signer=session_interface.use_signer,
            permanent=session_interface.permanent,
            **config,
        )
