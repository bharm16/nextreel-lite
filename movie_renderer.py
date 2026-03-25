"""Movie rendering logic backed by movie_projection rows."""

from quart import render_template

from logging_config import get_logger

logger = get_logger(__name__)


class MovieRenderer:
    """Handles template rendering for projection-backed movie payloads."""

    def __init__(self, projection_store):
        self.projection_store = projection_store

    async def render_movie_by_tconst(self, tconst, previous_count=0, template_name="movie.html"):
        movie_data = await self.projection_store.fetch_renderable_payload(tconst)
        if not movie_data:
            logger.info("No data found for movie with tconst: %s", tconst)
            return "Movie not found", 404

        return await render_template(
            template_name,
            movie=movie_data,
            previous_count=previous_count,
        )
