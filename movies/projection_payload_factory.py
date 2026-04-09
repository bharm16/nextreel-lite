"""Payload shaping helpers for projection rows."""

from __future__ import annotations

import json
from typing import Any

from movies.projection_state import ProjectionState

PLACEHOLDER_POSTER = "/static/img/poster-placeholder.svg"
PLACEHOLDER_BACKDROP = "/static/img/backdrop-placeholder.svg"


class ProjectionPayloadFactory:
    def payload_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload_json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("projection_state", row.get("projection_state"))
        payload.setdefault("tconst", row.get("tconst"))
        return payload

    @staticmethod
    def persisted_payload(payload: dict[str, Any]) -> dict[str, Any]:
        slimmed = dict(payload)
        slimmed.pop("images", None)
        slimmed.pop("credits", None)
        return slimmed

    def build_core_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        language = row.get("language") or "unknown"
        genres = row.get("genres") or "Unknown"
        rating = row.get("averageRating") or 0
        votes = row.get("numVotes") or 0
        return {
            "title": row.get("primaryTitle") or "Unknown",
            "imdb_id": row["tconst"],
            "tmdb_id": None,
            "slug": row.get("slug"),
            "genres": genres,
            "directors": "Unknown",
            "rating": float(rating),
            "votes": int(votes),
            "plot": "Additional details are still loading for this title.",
            "poster_url": PLACEHOLDER_POSTER,
            "year": str(row.get("startYear") or "Unknown"),
            "cast": [],
            "trailer": None,
            "backdrop_url": PLACEHOLDER_BACKDROP,
            "original_language": language,
            "spoken_languages": [language] if language != "unknown" else [],
            "age_rating": "Not Rated",
            "budget": "Unknown",
            "revenue": "Unknown",
            "runtime": "Unknown",
            "production_countries": "Unknown",
            "status": "Unknown",
            "tagline": "",
            "watch_providers": None,
            "key_crew": [],
            "keywords": [],
            "recommendations": [],
            "external_ids": {},
            "collection": None,
            "homepage": "",
            "_full": False,
            "projection_state": ProjectionState.CORE.value,
        }
