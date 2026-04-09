"""Movie payload assembly helpers."""

from __future__ import annotations

from typing import Any


class MoviePayloadFormatter:
    def assemble(
        self,
        *,
        full_data: dict[str, Any],
        ratings_data: dict[str, Any] | None,
        tmdb_helper,
        tconst: str,
        slug: str | None,
        tmdb_id: int,
    ) -> dict[str, Any]:
        cast = tmdb_helper.parse_cast(full_data)
        directors = tmdb_helper.parse_directors(full_data)
        key_crew = tmdb_helper.parse_key_crew(full_data)
        trailer = tmdb_helper.parse_trailer(full_data)
        images = tmdb_helper.parse_images(full_data)
        age_rating = tmdb_helper.parse_age_rating(full_data)
        watch_providers = tmdb_helper.parse_watch_providers(full_data)
        keywords = tmdb_helper.parse_keywords(full_data)
        recommendations = tmdb_helper.parse_recommendations(full_data)
        external_ids = tmdb_helper.parse_external_ids(full_data)
        collection = tmdb_helper.parse_collection(full_data)

        backdrop_url = images["backdrops"][0] if images.get("backdrops") else None

        rating = (
            ratings_data["averageRating"]
            if ratings_data and ratings_data["averageRating"] != "N/A"
            else full_data.get("vote_average", "N/A")
        )
        votes = (
            ratings_data["numVotes"]
            if ratings_data and ratings_data["numVotes"] != "N/A"
            else full_data.get("vote_count", "N/A")
        )

        budget = full_data.get("budget", 0)
        revenue = full_data.get("revenue", 0)
        budget_formatted = f"${budget:,}" if budget > 0 else "Unknown"
        revenue_formatted = f"${revenue:,}" if revenue > 0 else "Unknown"

        countries = full_data.get("production_countries", [])
        country_names = [country.get("name", "") for country in countries[:3]]

        return {
            "title": full_data.get("title", "N/A"),
            "imdb_id": tconst,
            "tmdb_id": tmdb_id,
            "slug": slug,
            "genres": ", ".join([genre["name"] for genre in full_data.get("genres", [])]),
            "directors": ", ".join(directors),
            "rating": rating,
            "votes": votes,
            "plot": full_data.get("overview", "N/A"),
            "poster_url": (
                f"{tmdb_helper.image_base_url}w500{full_data.get('poster_path')}"
                if full_data.get("poster_path")
                else None
            ),
            "year": (
                full_data.get("release_date", "N/A")[:4]
                if full_data.get("release_date")
                else "N/A"
            ),
            "cast": cast,
            "trailer": trailer,
            "backdrop_url": backdrop_url,
            "original_language": full_data.get("original_language", "unknown"),
            "spoken_languages": [
                lang.get("iso_639_1") for lang in full_data.get("spoken_languages", [])
            ],
            "age_rating": age_rating,
            "budget": budget_formatted,
            "revenue": revenue_formatted,
            "runtime": (
                f"{full_data.get('runtime', 0)} min" if full_data.get("runtime") else "Unknown"
            ),
            "production_countries": ", ".join(country_names) if country_names else "Unknown",
            "status": full_data.get("status", "Unknown"),
            "tagline": full_data.get("tagline", ""),
            "watch_providers": watch_providers,
            "key_crew": key_crew,
            "keywords": keywords,
            "recommendations": recommendations,
            "external_ids": external_ids,
            "collection": collection,
            "homepage": full_data.get("homepage", ""),
            "_full": True,
        }
