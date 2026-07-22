"""Deezer client: second, independent similar-artist source.

Deezer's public API has no auth token and no similarity score, only a
ranked "related artists" list, so we synthesize a rank-decayed score to
make it comparable to Last.fm's match value.
"""

from __future__ import annotations

import httpx

from lidarr_similar.models import Candidate

API_ROOT = "https://api.deezer.com"
SOURCE_NAME = "deezer"


class DeezerClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
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
