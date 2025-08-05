import json
from typing import Any, Dict, Optional

from logging_config import get_logger
from settings import DatabaseConnectionPool

logger = get_logger(__name__)


class SessionManager:
    """Handle persistence of anonymous user sessions."""

    def __init__(self, db_pool: DatabaseConnectionPool):
        self.db_pool = db_pool

    async def init(self) -> None:
        """Ensure the user_sessions table exists."""
        try:
            if not getattr(self.db_pool, "pool", None):
                await self.db_pool.init_pool()
            if not getattr(self.db_pool, "pool", None):
                return
            conn = await self.db_pool.get_async_connection()
            try:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS user_sessions (
                            user_id VARCHAR(36) PRIMARY KEY,
                            created_at DATETIME,
                            last_active DATETIME,
                            preferences JSON,
                            watch_history JSON,
                            favorites JSON,
                            device_fingerprint VARCHAR(64),
                            visit_count INT
                        )
                        """
                    )
                    await conn.commit()
            finally:
                await self.db_pool.release_async_connection(conn)
        except Exception:
            # During testing, the database may not be available.
            pass

    async def save_session(self, user_id: str, data: Dict[str, Any]) -> None:
        """Persist session information to the database."""
        conn = await self.db_pool.get_async_connection()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO user_sessions
                    (user_id, created_at, last_active, preferences, watch_history, favorites, device_fingerprint, visit_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_active=VALUES(last_active),
                        preferences=VALUES(preferences),
                        watch_history=VALUES(watch_history),
                        favorites=VALUES(favorites),
                        device_fingerprint=VALUES(device_fingerprint),
                        visit_count=VALUES(visit_count)
                    """,
                    (
                        user_id,
                        data.get("created_at"),
                        data.get("last_active"),
                        json.dumps(data.get("preferences", {})),
                        json.dumps(data.get("watch_history", [])),
                        json.dumps(data.get("favorites", [])),
                        data.get("device_fingerprint"),
                        data.get("visit_count", 0),
                    ),
                )
                await conn.commit()
        finally:
            await self.db_pool.release_async_connection(conn)

    async def load_by_fingerprint(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        conn = await self.db_pool.get_async_connection()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM user_sessions WHERE device_fingerprint=%s", (fingerprint,)
                )
                row = await cur.fetchone()
                if row:
                    row["preferences"] = json.loads(row.get("preferences") or "{}")
                    row["watch_history"] = json.loads(row.get("watch_history") or "[]")
                    row["favorites"] = json.loads(row.get("favorites") or "[]")
                return row
        finally:
            await self.db_pool.release_async_connection(conn)

    async def merge_sessions(self, primary_id: str, secondary_id: str) -> None:
        """Merge two session records into the primary user_id."""
        conn = await self.db_pool.get_async_connection()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE user_sessions u
                    JOIN user_sessions s ON s.user_id=%s
                    SET
                        u.preferences = JSON_MERGE_PATCH(u.preferences, s.preferences),
                        u.watch_history = JSON_ARRAY_DISTINCT(JSON_MERGE(u.watch_history, s.watch_history)),
                        u.favorites = JSON_ARRAY_DISTINCT(JSON_MERGE(u.favorites, s.favorites)),
                        u.visit_count = u.visit_count + s.visit_count
                    WHERE u.user_id=%s
                    """,
                    (secondary_id, primary_id),
                )
                await cur.execute("DELETE FROM user_sessions WHERE user_id=%s", (secondary_id,))
                await conn.commit()
        finally:
            await self.db_pool.release_async_connection(conn)

    async def recover_session(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        """Recover a session by fingerprint."""
        data = await self.load_by_fingerprint(fingerprint)
        if data:
            logger.info("Recovered session for fingerprint %s", fingerprint)
        return data

