"""Movie navigation logic — next/previous movie, session stacks, seen tracking."""

import asyncio

from quart import redirect, url_for, session, current_app

from logging_config import get_logger
from session.keys import (
    PREVIOUS_STACK_KEY, FUTURE_STACK_KEY, SEEN_TCONSTS_KEY,
    WATCH_QUEUE_KEY, CRITERIA_KEY, QUEUE_SIZE_KEY, CURRENT_MOVIE_KEY,
)

logger = get_logger(__name__)


def _is_language_accepted(movie_data: dict, desired_lang: str) -> bool:
    """Return True if *movie_data*'s language matches *desired_lang*."""
    if desired_lang == "any":
        return True
    original_lang = movie_data.get("original_language", "unknown")
    spoken_langs = movie_data.get("spoken_languages", [])
    if desired_lang == "en":
        return original_lang in ("en", "unknown", None) or "en" in spoken_langs
    return original_lang == desired_lang or desired_lang in spoken_langs


MAX_PREV_STACK_SIZE = 50  # Cap history to prevent unbounded session growth

# Keys kept in the lightweight session references. Everything else lives
# in the app cache keyed by tconst.
_REF_KEYS = ("imdb_id", "tmdb_id", "title", "slug")


def _movie_ref(movie_data: dict) -> dict:
    """Extract a lightweight reference suitable for session storage."""
    return {k: movie_data.get(k) for k in _REF_KEYS}


def _is_full_movie(entry: dict) -> bool:
    """Return True if *entry* is a full movie dict (vs a lightweight ref)."""
    return entry.get("_full", False)


async def _cache_movie_data(movie_data: dict) -> None:
    """Store full movie data in the app cache."""
    tconst = movie_data.get("imdb_id")
    if not tconst:
        return
    try:
        secure_cache = getattr(current_app, "secure_cache", None)
        if secure_cache:
            from infra.cache import CacheNamespace
            await secure_cache.set(
                CacheNamespace.MOVIE, f"full:{tconst}", movie_data, ttl=86400
            )
    except Exception as e:
        logger.debug("Failed to cache movie %s: %s", tconst, e)


async def _resolve_ref(ref: dict, db_pool=None, tmdb_helper=None) -> dict:
    """Resolve a lightweight ref to full movie data.

    Resolution order:
    1. Already full data (legacy session entry) — return as-is.
    2. App cache (Redis).
    3. Fresh TMDb + DB fetch (fallback when cache is empty/down).
    4. Return the bare ref so the renderer can do its own fetch.
    """
    # Already full data (legacy session entry)?
    if _is_full_movie(ref):
        return ref

    tconst = ref.get("imdb_id")
    if not tconst:
        return ref

    # Try cache first
    try:
        secure_cache = getattr(current_app, "secure_cache", None)
        if secure_cache:
            from infra.cache import CacheNamespace
            cached = await secure_cache.get(CacheNamespace.MOVIE, f"full:{tconst}")
            if cached:
                return cached
    except Exception as e:
        logger.debug("Cache lookup failed for %s: %s", tconst, e)

    # Cache miss — attempt a fresh fetch so navigation doesn't break when
    # Redis is unavailable or the TTL has expired.
    if db_pool:
        try:
            from movies.movie import Movie
            movie = Movie(tconst, db_pool, tmdb_helper=tmdb_helper)
            movie_data = await movie.get_movie_data()
            if movie_data:
                await _cache_movie_data(movie_data)
                return movie_data
        except Exception as e:
            logger.warning("Fallback fetch failed for %s: %s", tconst, e)

    # Last resort — return the bare ref; the renderer will attempt its own fetch
    return ref


class MovieNavigator:
    """Manages prev/next navigation and session-based movie stacks."""

    def __init__(self, movie_fetcher, db_pool, queue_size=2, tmdb_helper=None):
        self.movie_fetcher = movie_fetcher
        self.db_pool = db_pool
        self.queue_size = queue_size
        self.tmdb_helper = tmdb_helper

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
        from movies.movie import Movie

        queue = session.setdefault(WATCH_QUEUE_KEY, [])
        criteria = session.get(CRITERIA_KEY, {})
        limit = self.queue_size - len(queue)
        if limit <= 0:
            return

        fetch_limit = limit * 3 if criteria.get("min_year", 1900) >= 2024 else limit
        rows = await self.movie_fetcher.fetch_random_movies(criteria, fetch_limit)

        async def fetch_movie_data(row):
            movie = Movie(row["tconst"], self.db_pool, tmdb_helper=self.tmdb_helper)
            return await movie.get_movie_data()

        tasks = [fetch_movie_data(row) for row in rows]
        movie_results = await asyncio.gather(*tasks, return_exceptions=True)

        desired_lang = criteria.get("language", "en")

        for movie_data in movie_results:
            if movie_data and not isinstance(movie_data, Exception):
                if _is_language_accepted(movie_data, desired_lang):
                    # Cache full data, store lightweight ref in session queue
                    await _cache_movie_data(movie_data)
                    queue.append(_movie_ref(movie_data))

                if len(queue) >= session.get(QUEUE_SIZE_KEY, self.queue_size):
                    break

        session[WATCH_QUEUE_KEY] = queue

    async def load_initial_queue(self):
        """Public entry point for populating the queue from outside the navigator."""
        await self._load_movies_into_queue()

    async def _ensure_queue(self):
        queue = session.get(WATCH_QUEUE_KEY, [])
        if not queue:
            await self._load_movies_into_queue()

    async def next_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks()
        queue = session.setdefault(WATCH_QUEUE_KEY, [])

        current_movie = None

        if future_stack:
            ref = future_stack.pop()
            current_movie = await _resolve_ref(ref, db_pool=self.db_pool, tmdb_helper=self.tmdb_helper)
        elif queue:
            ref = queue.pop(0)
            current_movie = await _resolve_ref(ref, db_pool=self.db_pool, tmdb_helper=self.tmdb_helper)
        else:
            await self._load_movies_into_queue()
            queue = session.get(WATCH_QUEUE_KEY, [])
            if queue:
                ref = queue.pop(0)
                current_movie = await _resolve_ref(ref, db_pool=self.db_pool, tmdb_helper=self.tmdb_helper)

        previous = session.get(CURRENT_MOVIE_KEY)
        if previous and current_movie != previous:
            # Cache full data and store only a lightweight ref in the stack
            await _cache_movie_data(previous)
            prev_stack.append(_movie_ref(previous))
            # Trim oldest entries to cap session size
            if len(prev_stack) > MAX_PREV_STACK_SIZE:
                prev_stack = prev_stack[-MAX_PREV_STACK_SIZE:]

        # Store only a lightweight ref in the session; full data lives in cache.
        if current_movie:
            await _cache_movie_data(current_movie)
            session[CURRENT_MOVIE_KEY] = _movie_ref(current_movie)
        else:
            session[CURRENT_MOVIE_KEY] = None
        session[PREVIOUS_STACK_KEY] = prev_stack
        session[FUTURE_STACK_KEY] = future_stack
        session[WATCH_QUEUE_KEY] = queue

        if current_movie:
            tconst = current_movie.get("imdb_id")
            self._mark_movie_seen(tconst)
            logger.info("Navigating to next movie %s for user_id: %s", tconst, user_id)
            return redirect(url_for("main.movie_detail", tconst=tconst))
        else:
            logger.info("No next movie available for user_id: %s", user_id)
            return None

    async def previous_movie(self, user_id):
        prev_stack, future_stack = self._get_user_stacks()

        if not prev_stack:
            logger.info("No previous movies available for user_id: %s", user_id)
            return None

        current_movie = session.get(CURRENT_MOVIE_KEY)
        if current_movie:
            # Cache and store lightweight ref
            await _cache_movie_data(current_movie)
            future_stack.append(_movie_ref(current_movie))

        ref = prev_stack.pop()
        previous_movie = await _resolve_ref(ref, db_pool=self.db_pool, tmdb_helper=self.tmdb_helper)
        # Store lightweight ref in session; full data is in cache.
        if previous_movie:
            await _cache_movie_data(previous_movie)
            session[CURRENT_MOVIE_KEY] = _movie_ref(previous_movie)
        else:
            session[CURRENT_MOVIE_KEY] = None
        session[PREVIOUS_STACK_KEY] = prev_stack
        session[FUTURE_STACK_KEY] = future_stack

        tconst = previous_movie.get("imdb_id")
        if tconst:
            logger.info("Navigating to previous movie %s for user_id: %s", tconst, user_id)
            return redirect(url_for("main.movie_detail", tconst=tconst))
        else:
            logger.error("Previous movie missing imdb_id for user_id: %s", user_id)
            return None

    def get_current_movie_tconst(self):
        """Return the tconst of the currently displayed movie, or None."""
        current = session.get(CURRENT_MOVIE_KEY)
        if current:
            return current.get("imdb_id")
        return None

    # get_movie_by_slug removed — no route or service method calls it.
    # If slug-based navigation is needed in the future, re-implement with
    # cache-first resolution rather than linear session-stack scanning.
