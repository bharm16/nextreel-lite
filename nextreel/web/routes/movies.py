"""Movie display route handlers."""

from __future__ import annotations

from quart import abort, g, render_template

from infra.route_helpers import with_timeout
from nextreel.web.routes.shared import (
    _REQUEST_TIMEOUT,
    _TCONST_RE,
    _current_state,
    _current_user_id,
    _legacy_session,
    _movie_detail_blocks_partial_render,
    _movie_detail_service,
    _movie_image_context,
    _services,
    bp,
    logger,
)


@bp.route("/movie/<tconst>")
@with_timeout(_REQUEST_TIMEOUT)
async def movie_detail(tconst):
    if not _TCONST_RE.match(tconst):
        abort(400, "Invalid movie identifier")

    state = _current_state()
    user_id = _current_user_id()
    services = _services()
    movie_manager = services.movie_manager

    logger.debug(
        "Fetching movie details for tconst: %s, session_id: %s. Correlation ID: %s",
        tconst,
        state.session_id,
        g.correlation_id,
    )

    view_model = await _movie_detail_service.get(
        movie_manager=movie_manager,
        state=state,
        user_id=user_id,
        tconst=tconst,
    )

    if view_model is None:
        logger.info("No data found for movie with tconst: %s", tconst)
        return "Movie not found", 404

    if _movie_detail_blocks_partial_render() and not view_model.movie.get("_full"):
        logger.error(
            "Blocking partial movie detail render for %s (projection_state=%s)",
            tconst,
            view_model.movie.get("projection_state"),
        )
        return "Service temporarily unavailable", 503

    g.is_watched = view_model.is_watched
    image_context = _movie_image_context(view_model.movie)
    return await render_template(
        "movie.html",
        movie=view_model.movie,
        previous_count=view_model.previous_count,
        **image_context,
    )


@bp.route("/")
async def home():
    state = _current_state()
    data = await _services().movie_manager.home(state, legacy_session=_legacy_session())
    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
    )

__all__ = ["home", "movie_detail"]
