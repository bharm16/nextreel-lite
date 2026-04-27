"""Movie display route handlers."""

from __future__ import annotations

import random

from quart import abort, g, jsonify, redirect, render_template, request

from infra.route_helpers import rate_limited, with_timeout
from movies.landing_film_service import fetch_random_landing_film
from movies.landing_filter_url import (
    active_filters_for_template,
    criteria_from_query_args,
)
from movies.movie_url import build_movie_path, parse_movie_path, title_slug
from movies.public_id import public_id_for_tconst
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


@bp.route("/api/landing-film")
@rate_limited("landing_film_api")
@with_timeout(_REQUEST_TIMEOUT)
async def landing_film_json():
    """JSON endpoint for the landing-page filter pills.

    Reads URL filter params (genre, decade, runtime, rating), translates them
    to internal criteria via criteria_from_query_args, and returns one matching
    film payload as JSON. Returns 204 with empty body when no film matches the
    filter combination — the client-side JS handles the empty state.

    The response is a deliberate projection: it never exposes ``tconst`` (the
    IMDb id is internal post the public_id migration) and it always carries a
    server-built ``movie_path`` so the client doesn't have to reproduce the
    canonical-URL slugifier from movies/movie_url.py.

    Used by static/js/landing-pills.js for in-place hero reroll.
    """
    services = _services()
    criteria = criteria_from_query_args(request.args)
    film = await fetch_random_landing_film(services.movie_manager.db_pool, criteria)
    if film is None:
        return ("", 204)

    public_id = film.get("public_id")
    if not public_id:
        public_id = await public_id_for_tconst(
            services.movie_manager.db_pool, film.get("tconst")
        )
    if not public_id:
        # Projection should always carry a public_id post-backfill; if it
        # doesn't we can't build a canonical URL for the secondary CTA, so
        # treat as no-result rather than ship a broken link.
        logger.warning(
            "landing_film_json: dropping film with no public_id (tconst=%s)",
            film.get("tconst"),
        )
        return ("", 204)

    return jsonify(
        {
            "public_id": public_id,
            "title": film.get("title"),
            "year": film.get("year"),
            "director": film.get("director"),
            "runtime": film.get("runtime"),
            "backdrop_url": film.get("backdrop_url"),
            "movie_path": build_movie_path(
                film.get("title"), film.get("year"), public_id
            ),
        }
    )


@bp.route("/")
@with_timeout(_REQUEST_TIMEOUT)
async def home():
    state = _current_state()
    services = _services()
    data = await services.movie_manager.home(state, legacy_session=_legacy_session())

    criteria = criteria_from_query_args(request.args)
    landing_film = await fetch_random_landing_film(services.movie_manager.db_pool, criteria)

    if landing_film is None:
        # Only fall back when the user did NOT filter — explicit filters with
        # no matches mean we render the empty state, not a hardcoded film.
        if not criteria:
            landing_film = random.choice(_LANDING_FALLBACK_POOL)

    if isinstance(landing_film, dict) and not landing_film.get("public_id"):
        landing_film = dict(landing_film)
        landing_film["public_id"] = await public_id_for_tconst(
            services.movie_manager.db_pool, landing_film.get("tconst")
        )

    active_filters = active_filters_for_template(criteria)
    # Raw URL-arg dict (only the four keys the landing strip understands) for
    # template pill aria-pressed state. Distinct from active_filters, which is
    # form-schema-keyed for the /filtered_movie POST.
    url_filters = {
        k: request.args.get(k)
        for k in ("genre", "decade", "runtime", "rating")
        if request.args.get(k)
    }

    return await render_template(
        "home.html",
        default_backdrop_url=data["default_backdrop_url"],
        landing_film=landing_film,
        active_filters=active_filters,
        url_filters=url_filters,
    )


__all__ = ["home", "movie_detail"]
