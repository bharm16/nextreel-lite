"""TMDb API response parser — pure functions for extracting structured data.

Extracted from TMDbHelper to separate HTTP transport concerns from
response parsing logic (SRP). All methods are stateless and only depend
on the image_base_url string for URL construction.
"""

from __future__ import annotations

from typing import Any, Optional


TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"


class TMDbResponseParser:
    """Stateless parser for TMDb combined-response payloads.

    Every method receives the raw ``data`` dict returned by TMDb and
    extracts one slice of it.  No I/O, no side effects.
    """

    def __init__(self, image_base_url: str = TMDB_IMAGE_BASE_URL):
        self.image_base_url = image_base_url

    def parse_watch_providers(self, data: dict, region: str = "US") -> Optional[dict]:
        wp_data = data.get("watch/providers", {})
        region_data = wp_data.get("results", {}).get(region, {})
        if not region_data:
            return None

        providers: dict[str, Any] = {}
        for category, key in [
            ("stream", "flatrate"),
            ("rent", "rent"),
            ("buy", "buy"),
            ("ads", "ads"),
        ]:
            if key in region_data:
                providers[category] = [
                    {
                        "provider_name": p.get("provider_name"),
                        "logo_path": (
                            f"{self.image_base_url}w92{p.get('logo_path')}"
                            if p.get("logo_path")
                            else None
                        ),
                    }
                    for p in region_data[key][:4]
                ]

        if "link" in region_data:
            providers["justwatch_link"] = region_data["link"]

        return providers if providers else None

    def parse_age_rating(self, data: dict) -> str:
        rd_data = data.get("release_dates", {})
        for country in rd_data.get("results", []):
            if country.get("iso_3166_1") == "US":
                for release in country.get("release_dates", []):
                    cert = release.get("certification", "").strip()
                    if cert:
                        return cert
        for country in rd_data.get("results", []):
            for release in country.get("release_dates", []):
                cert = release.get("certification", "").strip()
                if cert:
                    return cert
        return "Not Rated"

    def parse_cast(self, data: dict, limit: int = 10) -> list[dict]:
        credits = data.get("credits", {})
        return [
            {
                "name": m["name"],
                "image_url": (
                    f"{self.image_base_url}w185{m['profile_path']}"
                    if m.get("profile_path")
                    else None
                ),
                "character": m.get("character", "N/A"),
            }
            for m in credits.get("cast", [])[:limit]
        ]

    def parse_directors(self, data: dict) -> list[str]:
        credits = data.get("credits", {})
        return [crew["name"] for crew in credits.get("crew", []) if crew.get("job") == "Director"]

    def parse_key_crew(self, data: dict) -> dict:
        credits = data.get("credits", {})
        crew_list = credits.get("crew", [])

        writers: list[str] = []
        composer = None
        cinematographer = None
        for member in crew_list:
            job = member.get("job", "")
            if job in ("Screenplay", "Writer") and len(writers) < 3:
                writers.append(member["name"])
            elif job == "Original Music Composer" and not composer:
                composer = member["name"]
            elif job == "Director of Photography" and not cinematographer:
                cinematographer = member["name"]

        result: dict[str, Any] = {}
        if writers:
            result["writers"] = writers
        if composer:
            result["composer"] = composer
        if cinematographer:
            result["cinematographer"] = cinematographer
        return result

    def parse_trailer(self, data: dict) -> Optional[str]:
        for video in data.get("videos", {}).get("results", []):
            if video.get("site") == "YouTube" and video.get("type") == "Trailer":
                return f"https://www.youtube.com/watch?v={video['key']}"
        return None

    def parse_images(self, data: dict, limit: int = 1) -> dict[str, list[str]]:
        images_data = data.get("images", {})
        posters = images_data.get("posters", [])[:limit]
        backdrops = images_data.get("backdrops", [])[:limit]
        return {
            "posters": [
                f"{self.image_base_url}original{img['file_path']}"
                for img in posters
                if "file_path" in img
            ],
            "backdrops": [
                f"{self.image_base_url}original{img['file_path']}"
                for img in backdrops
                if "file_path" in img
            ],
        }

    def parse_keywords(self, data: dict) -> list[str]:
        return [kw["name"] for kw in data.get("keywords", {}).get("keywords", [])]

    def parse_recommendations(self, data: dict, limit: int = 10) -> list[dict]:
        return [
            {
                "tmdb_id": m["id"],
                "title": m.get("title", ""),
                "year": m.get("release_date", "")[:4] if m.get("release_date") else "",
                "poster_url": (
                    f"{self.image_base_url}w342{m['poster_path']}" if m.get("poster_path") else None
                ),
                "vote_average": m.get("vote_average", 0),
            }
            for m in data.get("recommendations", {}).get("results", [])[:limit]
        ]

    def parse_external_ids(self, data: dict) -> dict[str, str]:
        ext = data.get("external_ids", {})
        result: dict[str, str] = {}
        if ext.get("imdb_id"):
            result["imdb_url"] = f"https://www.imdb.com/title/{ext['imdb_id']}/"
        if ext.get("wikidata_id"):
            result["wikidata_url"] = f"https://www.wikidata.org/wiki/{ext['wikidata_id']}"
        if ext.get("facebook_id"):
            result["facebook_url"] = f"https://www.facebook.com/{ext['facebook_id']}"
        if ext.get("instagram_id"):
            result["instagram_url"] = f"https://www.instagram.com/{ext['instagram_id']}"
        if ext.get("twitter_id"):
            result["twitter_url"] = f"https://x.com/{ext['twitter_id']}"
        return result

    def parse_collection(self, data: dict) -> Optional[dict]:
        coll = data.get("belongs_to_collection")
        if not coll:
            return None
        return {
            "id": coll["id"],
            "name": coll["name"],
            "poster_url": (
                f"{self.image_base_url}w185{coll['poster_path']}"
                if coll.get("poster_path")
                else None
            ),
        }
