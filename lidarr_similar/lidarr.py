"""Lidarr client: adds discovered artists to the library."""

from __future__ import annotations

import httpx

from lidarr_similar.models import Candidate


class LidarrClient:
    def __init__(self, url: str, api_key: str, http_client: httpx.AsyncClient | None = None) -> None:
        # httpx's 5s default timeout intermittently trips on /api/v1/artist for a
        # large library - confirmed live (962 artists: <5s failed, 15s succeeded).
        self._http = http_client or httpx.AsyncClient(
            base_url=url.rstrip("/"), headers={"X-Api-Key": api_key}, timeout=30.0
        )

    async def existing_artist_names(self) -> set[str]:
        return {artist["artistName"] for artist in await self._fetch_artists()}

    async def existing_artist_identifiers(self) -> tuple[set[str], set[str]]:
        """Names and MusicBrainz IDs (Lidarr's `foreignArtistId`) of everything already in the
        library, fetched in one request. MBIDs are an exact-identity match - preferred over
        name matching, which needs normalization and can still miss genuine spelling variants."""
        artists = await self._fetch_artists()
        names = {artist["artistName"] for artist in artists}
        mbids = {artist["foreignArtistId"] for artist in artists if artist.get("foreignArtistId")}
        return names, mbids

    async def _fetch_artists(self) -> list[dict]:
        response = await self._http.get("/api/v1/artist")
        response.raise_for_status()
        return response.json()

    async def quality_profiles(self) -> list[dict]:
        """Lidarr's configured quality profiles ({"id": int, "name": str, ...}), for
        offering a dropdown instead of asking the user to know the numeric ID by heart."""
        response = await self._http.get("/api/v1/qualityprofile")
        response.raise_for_status()
        return response.json()

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
