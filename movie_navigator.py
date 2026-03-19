"""Movie navigation logic — next/previous movie, session stacks, seen tracking."""

import asyncio
import time

from quart import redirect, url_for, session

from logging_config import get_logger

logger = get_logger(__name__)


class MovieNavigator:
    """Manages prev/next navigation and session-based movie stacks."""

    def __init__(self, movie_fetcher, db_pool, queue_size=2):
        self.movie_fetcher = movie_fetcher
        self.db_pool = db_pool
        self.queue_size = queue_size

    def _get_user_stacks(self):
        prev_stack = session.setdefault("previous_movies_stack", [])
        future_stack = session.setdefault("future_movies_stack", [])
        return prev_stack, future_stack

    def _mark_movie_seen(self, tconst):
        seen = set(session.get("seen_tconsts", []))
        if tconst:
            seen.add(tconst)
        session["seen_tconsts"] = list(seen)

    async def _load_movies_into_queue(self):
        from scripts.movie import Movie

        queue = session.setdefault("watch_queue", [])
        criteria = session.get("criteria", {})
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

                if len(queue) >= session.get("queue_size", self.queue_size):
                    break

        session["watch_queue"] = queue

    async def _ensure_queue(self):
        queue = session.get("watch_queue", [])
        if not queue:
            await self._load_movies_into_queue()

    async def next_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks()
        queue = session.setdefault("watch_queue", [])

        current_movie = None

        if future_stack:
            current_movie = future_stack.pop()
        elif queue:
            current_movie = queue.pop(0)
        else:
            await self._load_movies_into_queue()
            queue = session.get("watch_queue", [])
            if queue:
                current_movie = queue.pop(0)

        previous = session.get("current_movie")
        if previous and current_movie != previous:
            prev_stack.append(previous)

        session["current_movie"] = current_movie
        session["previous_movies_stack"] = prev_stack
        session["future_movies_stack"] = future_stack
        session["watch_queue"] = queue

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

        current_movie = session.get("current_movie")
        if current_movie:
            future_stack.append(current_movie)

        previous_movie = prev_stack.pop()
        session["current_movie"] = previous_movie
        session["previous_movies_stack"] = prev_stack
        session["future_movies_stack"] = future_stack

        tconst = previous_movie.get("imdb_id")
        if tconst:
            logger.info(f"Navigating to previous movie {tconst} for user_id: {user_id}")
            return redirect(url_for("main.movie_detail", tconst=tconst))
        else:
            logger.error(f"Previous movie missing imdb_id for user_id: {user_id}")
            return None

    async def get_movie_by_slug(self, user_id, slug):
        prev_stack, future_stack = self._get_user_stacks()

        for movie in future_stack:
            if movie.get("slug") == slug:
                return movie

        current_movie = session.get("current_movie")
        if current_movie and current_movie.get("slug") == slug:
            return current_movie

        for movie in prev_stack:
            if movie.get("slug") == slug:
                return movie

        for movie in session.get("watch_queue", []):
            if movie.get("slug") == slug:
                return movie

        return None
