"""Discogs enrichment: best-effort genre/style metadata attached to Last.fm candidates.

Never a source of candidates, never blocking — a miss or API error leaves
the candidate unchanged so the core Last.fm discovery flow is unaffected.
"""

from __future__ import annotations

import httpx

from lidarr_similar.cache import Cache
from lidarr_similar.models import Candidate

API_ROOT = "https://api.discogs.com"
CACHE_SOURCE = "discogs"


class DiscogsEnricher:
    def __init__(
        self,
        token: str,
        cache: Cache,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._cache = cache
        self._http = http_client or httpx.AsyncClient(base_url=API_ROOT)

    async def enrich(self, candidate: Candidate) -> Candidate:
        cached = self._cache.get(CACHE_SOURCE, candidate.name)
        if cached is not None:
            return _apply(candidate, cached)

        try:
            artist_id = await self._find_artist_id(candidate.name)
            metadata = await self._fetch_genre_style(candidate.name)
            metadata["discogs_id"] = artist_id
        except httpx.HTTPError:
            return candidate

        if artist_id is None and not metadata["discogs_genres"]:
            return candidate

        self._cache.set(CACHE_SOURCE, candidate.name, metadata)
        return _apply(candidate, metadata)

    async def _find_artist_id(self, name: str) -> int | None:
        response = await self._http.get(
            "/database/search",
            params={"q": name, "type": "artist", "token": self._token},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        exact = next((r for r in results if r.get("title", "").lower() == name.lower()), None)
        return (exact or results[0])["id"]

    async def _fetch_genre_style(self, artist_name: str) -> dict:
        """Discogs artist objects carry no genre/style; those only exist on releases,
        so look up the artist's releases, newest first, and take the top match's
        genre/style/year. The year is a best-effort "last release" signal - it
        reflects a specific pressing's catalog year, not necessarily an original
        release date, but is the closest Discogs offers without per-release lookups.
        """
        response = await self._http.get(
            "/database/search",
            params={
                "artist": artist_name,
                "type": "release",
                "sort": "year",
                "sort_order": "desc",
                "token": self._token,
            },
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return {"discogs_genres": [], "discogs_styles": [], "discogs_latest_release_year": None}
        return {
            "discogs_genres": results[0].get("genre", []),
            "discogs_styles": results[0].get("style", []),
            "discogs_latest_release_year": results[0].get("year") or None,
        }

    async def aclose(self) -> None:
        await self._http.aclose()


def _apply(candidate: Candidate, metadata: dict) -> Candidate:
    candidate.discogs_id = metadata.get("discogs_id")
    candidate.discogs_genres = metadata.get("discogs_genres", [])
    candidate.discogs_styles = metadata.get("discogs_styles", [])
    candidate.discogs_latest_release_year = metadata.get("discogs_latest_release_year")
    return candidate
