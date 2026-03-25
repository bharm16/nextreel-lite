"""Movie rendering logic — template rendering for movie display."""

from quart import render_template, session

from logging_config import get_logger
from movies.movie import Movie
from session.keys import CURRENT_MOVIE_KEY

logger = get_logger(__name__)


class MovieRenderer:
    """Handles rendering movie data into templates."""

    def __init__(self, db_pool, tmdb_helper):
        self.db_pool = db_pool
        self.tmdb_helper = tmdb_helper

    async def fetch_and_render_movie(
        self, current_displayed_movie, user_id, prev_stack_len, template_name="movie.html"
    ):
        if not current_displayed_movie:
            logger.debug("No current movie to display for user_id: %s", user_id)
            return None

        if (
            "backdrop_url" in current_displayed_movie
            and current_displayed_movie["backdrop_url"]
        ):
            return await render_template(
                template_name,
                movie=current_displayed_movie,
                previous_count=prev_stack_len,
            )

        logger.debug(
            "Movie skipped due to missing backdrop image for user_id: %s", user_id
        )
        return None

    async def render_movie_by_tconst(self, user_id, tconst, template_name="movie.html"):
        # Try app cache first (full movie data lives there, not in session).
        try:
            from quart import current_app
            secure_cache = getattr(current_app, "secure_cache", None)
            if secure_cache:
                from infra.cache import CacheNamespace
                cached = await secure_cache.get(CacheNamespace.MOVIE, f"full:{tconst}")
                if cached and cached.get("_full"):
                    logger.debug(
                        "Using cached data for movie %s (user_id: %s)", tconst, user_id
                    )
                    return await render_template(template_name, movie=cached)
        except Exception as e:
            logger.debug("Cache lookup failed for %s: %s", tconst, e)

        # Fallback: fetch fresh data (direct URL navigation or cache miss).
        movie_instance = Movie(tconst, self.db_pool, tmdb_helper=self.tmdb_helper)
        movie_data = await movie_instance.get_movie_data()
        if not movie_data:
            logger.info(
                "No data found for movie with tconst: %s and user_id: %s", tconst, user_id
            )
            return "Movie not found", 404

        # Populate cache for subsequent renders
        from movie_navigator import cache_movie_data
        await cache_movie_data(movie_data)

        return await render_template(template_name, movie=movie_data)
