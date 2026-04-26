"""Shared filter-state and criteria contracts.

These types intentionally preserve the current split between:
- UI/session/persisted filter state (`FilterState`)
- query-layer criteria (`MovieCriteria`)
"""

from __future__ import annotations

from typing import TypedDict


class FilterState(TypedDict, total=False):
    year_min: int | str
    year_max: int | str
    imdb_score_min: float | str
    imdb_score_max: float | str
    num_votes_min: int | str
    num_votes_max: int | str
    language: str
    genres_selected: list[str]
    exclude_watched: bool
    exclude_watchlist: bool


class MovieCriteria(TypedDict, total=False):
    title_type: str
    min_year: int
    max_year: int
    min_rating: float
    max_rating: float
    min_votes: int
    max_votes: int
    language: str
    genres: list[str]
