"""Last.fm client: source of truth for scrobbles and artist similarity."""

from __future__ import annotations

import httpx

from lidarr_similar.models import Candidate

API_ROOT = "https://ws.audioscrobbler.com/2.0/"
SOURCE_NAME = "lastfm"


class LastFmClient:
    def __init__(self, api_key: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._http = http_client or httpx.AsyncClient(base_url=API_ROOT)

    async def top_artists(self, username: str, limit: int = 50) -> list[str]:
        data = await self._get(
            method="user.gettopartists", user=username, limit=str(limit), period="6month"
        )
        artists = data.get("topartists", {}).get("artist", [])
        return [artist["name"] for artist in artists]

    async def similar_artists(self, artist: str, limit: int = 10) -> list[Candidate]:
        data = await self._get(method="artist.getsimilar", artist=artist, limit=str(limit))
        matches = data.get("similarartists", {}).get("artist", [])
        return [
            Candidate(
                name=match["name"],
                similarity=float(match.get("match", 0.0)),
                sources=[SOURCE_NAME],
                mbid=match.get("mbid") or None,
            )
            for match in matches
        ]

    async def _get(self, **params: str) -> dict:
        response = await self._http.get(
            "", params={**params, "api_key": self._api_key, "format": "json"}
        )
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()
