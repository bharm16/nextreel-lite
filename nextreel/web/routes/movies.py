"""Movie display route handlers."""

from __future__ import annotations

import random

from quart import abort, g, redirect, render_template

from infra.route_helpers import with_timeout
from movies.landing_film_service import fetch_random_landing_film
from movies.movie_url import build_movie_path, parse_movie_path, title_slug
from nextreel.web.routes.shared import (
    _REQUEST_TIMEOUT,
    _current_state,
    _current_user_id,
    _legacy_session,
    _movie_detail_service,
    _movie_image_context,
    _resolve_public_id_or_404,
    _services,
    bp,
    logger,
)


# Verified against the live movie_projection table on 2026-04-17. Each entry
# is an enriched READY row with a valid TMDb backdrop URL that returns 200,
# so the "See this film ↗" secondary CTA also resolves to an existing
# movie_detail page.
_LANDING_FALLBACK_POOL = (
    {
        "tconst": "tt0062622",
        "title": "2001: A Space Odyssey",
        "year": "1968",
        "director": "Stanley Kubrick",
        "runtime": "149 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/9yTOU2SvTfAEHDPEG5qraLoe4MI.jpg",
    },
    {
        "tconst": "tt0078748",
        "title": "Alien",
        "year": "1979",
        "director": "Ridley Scott",
        "runtime": "117 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/AmR3JG1VQVxU8TfAvljUhfSFUOx.jpg",
    },
    {
        "tconst": "tt0110912",
        "title": "Pulp Fiction",
        "year": "1994",
        "director": "Quentin Tarantino",
        "runtime": "154 min",
        "backdrop_url": "https://image.tmdb.org/t/p/original/suaEOtk1N1sgg2MTM7oZd2cfVp3.jpg",
    },
)


@bp.route("/movie/<slug_with_id>")
@with_timeout(_REQUEST_TIMEOUT)
async def movie_detail(slug_with_id):
    parsed = parse_movie_path(slug_with_id)
    if parsed is None:
        abort(404)
    requested_slug, public_id = parsed

    tconst = await _resolve_public_id_or_404(public_id)

    state = _current_state()
    user_id = _current_user_id()
    services = _services()
    movie_manager = services.movie_manager

    logger.debug(
        "Fetching movie details for public_id: %s (tconst=%s), session_id: %s. "
        "Correlation ID: %s",
        public_id,
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
        logger.info("No data found for movie with public_id: %s (tconst=%s)", public_id, tconst)
        abort(404)

    if not view_model.movie.get("_full"):
        logger.warning(
            "Rendering partial movie detail for %s (projection_state=%s)",
            tconst,
            view_model.movie.get("projection_state"),
        )

    # Refuse to render an "untitled" canonical URL. A movie with no title
    # would slug to /movie/untitled-<public_id> — unguessable, misleading,
    # and indistinguishable across multiple no-title rows. 404 is the
    # honest response while we figure out why a payload arrived titleless.
    movie_title = view_model.movie.get("primaryTitle") or view_model.movie.get("title")
    if not movie_title or not str(movie_title).strip():
        logger.warning(
            "movie_detail: refusing to render no-title row tconst=%s public_id=%s",
            tconst,
            public_id,
        )
        abort(404)

    canonical_slug = title_slug(movie_title, view_model.movie.get("year"))
    if requested_slug != canonical_slug:
        return redirect(
            build_movie_path(movie_title, view_model.movie.get("year"), public_id),
            code=301,
        )

    g.is_watched = view_model.is_watched
    g.is_in_watchlist = view_model.is_in_watchlist
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
    services = _services()
    data = await services.movie_manager.home(state, legacy_session=_legacy_session())

    landing_film = await fetch_random_landing_film(services.movie_manager.db_pool)
    if landing_film is None:
        # DB-backed pick failed (transient error or empty READY set). Serve a
        # hardcoded fallback so the hero panel still renders. The fallback
        # entries intentionally lack public_id — querying the DB for one
        # would just compound the failure that got us here, and the home
        # template hides the "See this film ↗" CTA when public_id is missing.
        logger.warning(
            "Landing-film DB fetch returned None; serving hardcoded fallback"
        )
        landing_film = random.choice(_LANDING_FALLBACK_POOL)

    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
        landing_film=landing_film,
    )


__all__ = ["home", "movie_detail"]
