"""Application service for binding authenticated users to navigation sessions."""

from __future__ import annotations

from session import user_preferences


class AuthenticatedSessionBinder:
    async def bind_user(
        self,
        *,
        db_pool,
        navigation_state_store,
        state,
        user_id: str,
    ):
        exclude_watched = await user_preferences.get_exclude_watched_default(
            db_pool,
            user_id,
        )
        return await navigation_state_store.bind_user(
            state,
            user_id,
            exclude_watched=exclude_watched,
        )
