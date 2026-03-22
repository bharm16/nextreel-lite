"""Movie rendering logic — template rendering for movie display."""

from quart import render_template, session

from logging_config import get_logger
from scripts.movie import Movie
from session_keys import CURRENT_MOVIE_KEY

logger = get_logger(__name__)


class MovieRenderer:
    """Handles rendering movie data into templates."""

    def __init__(self, db_pool, tmdb_helper):
        self.db_pool = db_pool
        self.tmdb_helper = tmdb_helper

    async def render_home(self, default_backdrop_url):
        return await render_template(
            "home.html", default_backdrop_url=default_backdrop_url
        )

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
        # Check if we already have this movie's full data in session (avoids
        # redundant TMDb API calls when navigating via next/previous).
        current = session.get(CURRENT_MOVIE_KEY)
        if current and current.get("imdb_id") == tconst and "plot" in current:
            logger.debug(
                "Using cached session data for movie %s (user_id: %s)", tconst, user_id
            )
            return await render_template(template_name, movie=current)

        # Fallback: fetch fresh data (e.g. direct URL navigation, or
        # lightweight ref that couldn't be resolved from cache).
        movie_instance = Movie(tconst, self.db_pool, tmdb_helper=self.tmdb_helper)
        movie_data = await movie_instance.get_movie_data()
        if not movie_data:
            logger.info(
                "No data found for movie with tconst: %s and user_id: %s", tconst, user_id
            )
            return "Movie not found", 404

        # Backfill session so subsequent renders don't re-fetch
        session[CURRENT_MOVIE_KEY] = movie_data

        return await render_template(template_name, movie=movie_data)
