"""Movie navigation logic — next/previous movie, session stacks, seen tracking."""

import asyncio
import time

from quart import redirect, url_for, session

from logging_config import get_logger
from session_keys import (
    PREVIOUS_STACK_KEY, FUTURE_STACK_KEY, SEEN_TCONSTS_KEY,
    WATCH_QUEUE_KEY, CRITERIA_KEY, QUEUE_SIZE_KEY, CURRENT_MOVIE_KEY,
)

logger = get_logger(__name__)


MAX_PREV_STACK_SIZE = 50  # Cap history to prevent unbounded session growth


class MovieNavigator:
    """Manages prev/next navigation and session-based movie stacks."""

    def __init__(self, movie_fetcher, db_pool, queue_size=2):
        self.movie_fetcher = movie_fetcher
        self.db_pool = db_pool
        self.queue_size = queue_size

    def _get_user_stacks(self):
        prev_stack = session.setdefault(PREVIOUS_STACK_KEY, [])
        future_stack = session.setdefault(FUTURE_STACK_KEY, [])
        return prev_stack, future_stack

    def _mark_movie_seen(self, tconst):
        seen_list = session.get(SEEN_TCONSTS_KEY, [])
        if tconst and tconst not in seen_list:
            seen_list.append(tconst)
            # Cap to prevent unbounded session growth
            if len(seen_list) > MAX_PREV_STACK_SIZE * 2:
                seen_list = seen_list[-(MAX_PREV_STACK_SIZE * 2):]
            session[SEEN_TCONSTS_KEY] = seen_list

    async def _load_movies_into_queue(self):
        from scripts.movie import Movie

        queue = session.setdefault(WATCH_QUEUE_KEY, [])
        criteria = session.get(CRITERIA_KEY, {})
        limit = self.queue_size - len(queue)
        if limit <= 0:
            return

        fetch_limit = limit * 3 if criteria.get("min_year", 1900) >= 2024 else limit
        rows = await self.movie_fetcher.fetch_random_movies(criteria, fetch_limit)

        async def fetch_movie_data(row):
            movie = Movie(row["tconst"], self.db_pool)
            return await movie.get_movie_data()

        tasks = [fetch_movie_data(row) for row in rows]
        movie_results = await asyncio.gather(*tasks, return_exceptions=True)

        desired_lang = criteria.get("language", "en")

        for movie_data in movie_results:
            if movie_data and not isinstance(movie_data, Exception):
                if desired_lang == "any":
                    queue.append(movie_data)
                else:
                    original_lang = movie_data.get("original_language", "unknown")
                    spoken_langs = movie_data.get("spoken_languages", [])

                    if desired_lang == "en":
                        if original_lang in ["en", "unknown", None] or "en" in spoken_langs:
                            queue.append(movie_data)
                    elif original_lang == desired_lang or desired_lang in spoken_langs:
                        queue.append(movie_data)

                if len(queue) >= session.get(QUEUE_SIZE_KEY, self.queue_size):
                    break

        session[WATCH_QUEUE_KEY] = queue

    async def _ensure_queue(self):
        queue = session.get(WATCH_QUEUE_KEY, [])
        if not queue:
            await self._load_movies_into_queue()

    async def next_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks()
        queue = session.setdefault(WATCH_QUEUE_KEY, [])

        current_movie = None

        if future_stack:
            current_movie = future_stack.pop()
        elif queue:
            current_movie = queue.pop(0)
        else:
            await self._load_movies_into_queue()
            queue = session.get(WATCH_QUEUE_KEY, [])
            if queue:
                current_movie = queue.pop(0)

        previous = session.get(CURRENT_MOVIE_KEY)
        if previous and current_movie != previous:
            prev_stack.append(previous)
            # Trim oldest entries to cap session size
            if len(prev_stack) > MAX_PREV_STACK_SIZE:
                prev_stack = prev_stack[-MAX_PREV_STACK_SIZE:]

        session[CURRENT_MOVIE_KEY] = current_movie
        session[PREVIOUS_STACK_KEY] = prev_stack
        session[FUTURE_STACK_KEY] = future_stack
        session[WATCH_QUEUE_KEY] = queue

        if current_movie:
            tconst = current_movie.get("imdb_id")
            self._mark_movie_seen(tconst)
            logger.info(f"Navigating to next movie {tconst} for user_id: {user_id}")
            return redirect(url_for("main.movie_detail", tconst=tconst))
        else:
            logger.info(f"No next movie available for user_id: {user_id}")
            return None

    async def previous_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks()

        if not prev_stack:
            logger.info(f"No previous movies available for user_id: {user_id}")
            return None

        current_movie = session.get(CURRENT_MOVIE_KEY)
        if current_movie:
            future_stack.append(current_movie)

        previous_movie = prev_stack.pop()
        session[CURRENT_MOVIE_KEY] = previous_movie
        session[PREVIOUS_STACK_KEY] = prev_stack
        session[FUTURE_STACK_KEY] = future_stack

        tconst = previous_movie.get("imdb_id")
        if tconst:
            logger.info(f"Navigating to previous movie {tconst} for user_id: {user_id}")
            return redirect(url_for("main.movie_detail", tconst=tconst))
        else:
            logger.error(f"Previous movie missing imdb_id for user_id: {user_id}")
            return None

    def get_current_movie_tconst(self):
        """Return the tconst of the currently displayed movie, or None."""
        current = session.get(CURRENT_MOVIE_KEY)
        if current:
            return current.get("imdb_id")
        return None

    async def get_movie_by_slug(self, user_id, slug):
        prev_stack, future_stack = self._get_user_stacks()

        for movie in future_stack:
            if movie.get("slug") == slug:
                return movie

        current_movie = session.get(CURRENT_MOVIE_KEY)
        if current_movie and current_movie.get("slug") == slug:
            return current_movie

        for movie in prev_stack:
            if movie.get("slug") == slug:
                return movie

        for movie in session.get(WATCH_QUEUE_KEY, []):
            if movie.get("slug") == slug:
                return movie

        return None
