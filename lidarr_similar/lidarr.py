"""Lidarr client: adds discovered artists to the library."""

from __future__ import annotations

import httpx

from lidarr_similar.models import Candidate


class LidarrClient:
    def __init__(self, url: str, api_key: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(
            base_url=url.rstrip("/"), headers={"X-Api-Key": api_key}
        )

    async def existing_artist_names(self) -> set[str]:
        response = await self._http.get("/api/v1/artist")
        response.raise_for_status()
        return {artist["artistName"] for artist in response.json()}

    async def lookup_artist(self, name: str) -> dict | None:
        response = await self._http.get("/api/v1/artist/lookup", params={"term": name})
        response.raise_for_status()
        results = response.json()
        return results[0] if results else None

    async def add_artist(self, candidate: Candidate, root_folder: str, quality_profile_id: int) -> None:
        lookup = await self.lookup_artist(candidate.name)
        if lookup is None:
            return
        payload = {
            **lookup,
            "rootFolderPath": root_folder,
            "qualityProfileId": quality_profile_id,
            "monitored": True,
            "addOptions": {"searchForMissingAlbums": True},
        }
        response = await self._http.post("/api/v1/artist", json=payload)
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._http.aclose()
