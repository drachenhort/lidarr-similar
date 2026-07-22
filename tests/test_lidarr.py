from __future__ import annotations

import json

import httpx
import respx

from lidarr_similar.lidarr import LidarrClient
from lidarr_similar.models import Candidate

ARTISTS_URL = "http://lidarr.local/api/v1/artist"


@respx.mock
async def test_existing_artist_names():
    respx.get(ARTISTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"artistName": "VNV Nation", "foreignArtistId": "abc-123"},
                {"artistName": "Gunship", "foreignArtistId": "def-456"},
            ],
        )
    )

    client = LidarrClient("http://lidarr.local", "key")
    names = await client.existing_artist_names()

    assert names == {"VNV Nation", "Gunship"}


@respx.mock
async def test_existing_artist_identifiers_returns_names_and_mbids():
    respx.get(ARTISTS_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"artistName": "VNV Nation", "foreignArtistId": "abc-123"},
                {"artistName": "Gunship", "foreignArtistId": "def-456"},
            ],
        )
    )

    client = LidarrClient("http://lidarr.local", "key")
    names, mbids = await client.existing_artist_identifiers()

    assert names == {"VNV Nation", "Gunship"}
    assert mbids == {"abc-123", "def-456"}


@respx.mock
async def test_existing_artist_identifiers_skips_missing_mbid():
    respx.get(ARTISTS_URL).mock(
        return_value=httpx.Response(200, json=[{"artistName": "No MBID Artist", "foreignArtistId": ""}])
    )

    client = LidarrClient("http://lidarr.local", "key")
    names, mbids = await client.existing_artist_identifiers()

    assert names == {"No MBID Artist"}
    assert mbids == set()


@respx.mock
async def test_quality_profiles_returns_id_and_name():
    respx.get("http://lidarr.local/api/v1/qualityprofile").mock(
        return_value=httpx.Response(
            200, json=[{"id": 1, "name": "Any"}, {"id": 2, "name": "Lossless"}, {"id": 3, "name": "Standard"}]
        )
    )

    client = LidarrClient("http://lidarr.local", "key")
    profiles = await client.quality_profiles()

    assert profiles == [{"id": 1, "name": "Any"}, {"id": 2, "name": "Lossless"}, {"id": 3, "name": "Standard"}]


@respx.mock
async def test_metadata_profiles_returns_id_and_name():
    respx.get("http://lidarr.local/api/v1/metadataprofile").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Standard"}, {"id": 2, "name": "None"}])
    )

    client = LidarrClient("http://lidarr.local", "key")
    profiles = await client.metadata_profiles()

    assert profiles == [{"id": 1, "name": "Standard"}, {"id": 2, "name": "None"}]


@respx.mock
async def test_add_artist_includes_metadata_profile_id_in_payload():
    # Found live: Lidarr's /api/v1/artist rejects the request with 400 "'Metadata Profile
    # Id' must be greater than '0'" if metadataProfileId is missing from the payload.
    respx.get("http://lidarr.local/api/v1/artist/lookup").mock(
        return_value=httpx.Response(200, json=[{"artistName": "Diorama", "foreignArtistId": "abc"}])
    )
    add_route = respx.post(ARTISTS_URL).mock(return_value=httpx.Response(201, json={}))

    client = LidarrClient("http://lidarr.local", "key")
    await client.add_artist(
        Candidate(name="Diorama", similarity=0.9), root_folder="/music", quality_profile_id=3, metadata_profile_id=1
    )

    body = json.loads(add_route.calls.last.request.content)
    assert body["qualityProfileId"] == 3
    assert body["metadataProfileId"] == 1


def test_default_http_client_uses_a_generous_timeout():
    # Found live: httpx's 5s default intermittently timed out on /api/v1/artist for a
    # 962-artist library (failed under 5s, succeeded at 15s), silently aborting discovery.
    client = LidarrClient("http://lidarr.local", "key")

    assert client._http.timeout.read >= 30.0
