"""Deezer client: second, independent similar-artist source, plus optional genre enrichment.

Deezer's public API has no auth token and no similarity score, only a
ranked "related artists" list, so we synthesize a rank-decayed score to
make it comparable to Last.fm's match value.

Deezer artist objects carry no genre field either - only albums do, via
genre_id - so genre enrichment resolves an artist's top album's genre_id
to a genre name. It's a single, coarse genre (vs. Discogs' genre+style
pair), but needs no token and shares the same best-effort, non-blocking
contract as DiscogsEnricher.
"""

from __future__ import annotations

import httpx

from lidarr_similar.cache import Cache
from lidarr_similar.models import Candidate

API_ROOT = "https://api.deezer.com"
SOURCE_NAME = "deezer"
GENRE_CACHE_SOURCE = "deezer_genre"


class DeezerClient:
    def __init__(self, cache: Cache | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self._cache = cache
        self._http = http_client or httpx.AsyncClient(base_url=API_ROOT)

    async def similar_artists(self, artist: str, limit: int = 10) -> list[Candidate]:
        artist_id = await self._find_artist_id(artist)
        if artist_id is None:
            return []

        response = await self._http.get(f"/artist/{artist_id}/related")
        response.raise_for_status()
        related = response.json().get("data", [])[:limit]

        return [
            Candidate(name=entry["name"], similarity=_rank_score(rank), sources=[SOURCE_NAME])
            for rank, entry in enumerate(related)
        ]

    async def enrich_genre(self, candidate: Candidate) -> Candidate:
        """Best-effort genre attach; returns candidate unchanged on miss/error."""
        if self._cache is not None:
            cached = self._cache.get(GENRE_CACHE_SOURCE, candidate.name)
            if cached is not None:
                candidate.deezer_genre = cached
                return candidate

        try:
            genre = await self._fetch_genre(candidate.name)
        except httpx.HTTPError:
            return candidate

        if genre is None:
            return candidate

        if self._cache is not None:
            self._cache.set(GENRE_CACHE_SOURCE, candidate.name, genre)
        candidate.deezer_genre = genre
        return candidate

    async def _fetch_genre(self, artist_name: str) -> str | None:
        artist_id = await self._find_artist_id(artist_name)
        if artist_id is None:
            return None

        response = await self._http.get(f"/artist/{artist_id}/albums", params={"limit": 1})
        response.raise_for_status()
        albums = response.json().get("data", [])
        genre_id = albums[0].get("genre_id") if albums else None
        if not genre_id or genre_id < 0:
            return None

        response = await self._http.get(f"/genre/{genre_id}")
        response.raise_for_status()
        return response.json().get("name")

    async def _find_artist_id(self, name: str) -> int | None:
        response = await self._http.get("/search/artist", params={"q": name})
        response.raise_for_status()
        results = response.json().get("data", [])
        if not results:
            return None
        exact = next((r for r in results if r.get("name", "").lower() == name.lower()), None)
        return (exact or results[0])["id"]

    async def aclose(self) -> None:
        await self._http.aclose()


def _rank_score(rank: int, decay: float = 0.9) -> float:
    """1st related artist scores 1.0, decaying geometrically so scores stay in Last.fm's 0-1 range."""
    return decay**rank
