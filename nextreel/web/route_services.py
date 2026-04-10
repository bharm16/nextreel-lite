"""Route-facing services and presenters."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class MovieDetailViewModel:
    movie: dict
    previous_count: int
    is_watched: bool


@dataclass(slots=True)
class WatchedListViewModel:
    movies: list[dict]
    stats: dict
    total: int
    pagination: dict


class MovieDetailService:
    async def get(self, *, movie_manager, state, user_id: str | None, tconst: str):
        async def _watched_lookup() -> bool:
            if not user_id:
                return False
            return await movie_manager.watched_store.is_watched(user_id, tconst)

        async def _payload_lookup():
            return await movie_manager.projection_store.fetch_renderable_payload(tconst)

        is_watched, movie = await asyncio.gather(
            _watched_lookup(),
            _payload_lookup(),
        )
        if not movie:
            return None
        movie = dict(movie)
        if not movie.get("tconst"):
            movie["tconst"] = movie.get("imdb_id") or tconst

        return MovieDetailViewModel(
            movie=movie,
            previous_count=movie_manager.prev_stack_length(state),
            is_watched=bool(is_watched),
        )


class WatchedListPresenter:
    def build(self, *, raw_rows, total_count: int, page: int, per_page: int, now: datetime):
        movies: list[dict] = []
        year_values: list[int] = []
        this_month_count = 0

        for row in raw_rows:
            movie, year_int, is_this_month = self._normalize_row(row, now)
            if movie is None:
                continue
            if year_int:
                year_values.append(year_int)
            if is_this_month:
                this_month_count += 1
            movies.append(movie)

        total_pages = max(1, (total_count + per_page - 1) // per_page)
        return WatchedListViewModel(
            movies=movies,
            stats=self._build_stats(total_count, this_month_count, year_values),
            total=total_count,
            pagination={
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        )

    def _normalize_row(self, row, now: datetime) -> tuple[dict | None, int | None, bool]:
        payload = row.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}

        tconst = row.get("tconst")
        if not tconst:
            return None, None, False

        title = payload.get("title") or row.get("primaryTitle") or "Untitled"
        slug = payload.get("slug") or row.get("slug")

        year_raw = payload.get("year") or row.get("startYear")
        try:
            year_int = int(str(year_raw)[:4]) if year_raw else None
        except (TypeError, ValueError):
            year_int = None

        try:
            tmdb_rating = float(payload.get("rating") or 0)
        except (TypeError, ValueError):
            tmdb_rating = 0.0

        poster_url = payload.get("poster_url") or "/static/img/poster-placeholder.svg"

        watched_at = row.get("watched_at")
        watched_iso = watched_at.isoformat() if hasattr(watched_at, "isoformat") else str(watched_at or "")
        is_this_month = (
            hasattr(watched_at, "year")
            and watched_at.year == now.year
            and watched_at.month == now.month
        )

        return (
            {
                "tconst": tconst,
                "slug": slug,
                "title": title,
                "year": year_int,
                "poster_url": poster_url,
                "tmdb_rating": tmdb_rating,
                "watched_at": watched_iso,
            },
            year_int,
            is_this_month,
        )

    def _build_stats(self, total: int, this_month_count: int, year_values: list[int]) -> dict:
        avg_year = int(round(sum(year_values) / len(year_values))) if year_values else None
        if year_values:
            decade_counts: dict[int, int] = {}
            for year in year_values:
                decade = (year // 10) * 10
                decade_counts[decade] = decade_counts.get(decade, 0) + 1
            top_decade_year = max(decade_counts.items(), key=lambda item: (item[1], item[0]))[0]
            top_decade = "%ds" % top_decade_year
        else:
            top_decade = None

        return {
            "total": total,
            "this_month": this_month_count,
            "avg_year": avg_year,
            "top_decade": top_decade,
        }


class WatchedMutationService:
    async def add(self, *, user_id: str, tconst: str, watched_store) -> None:
        await watched_store.add(user_id, tconst)

    async def remove(self, *, user_id: str, tconst: str, watched_store) -> None:
        await watched_store.remove(user_id, tconst)
