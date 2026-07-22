from __future__ import annotations

import httpx
import respx

from lidarr_similar.deezer import DeezerClient


@respx.mock
async def test_similar_artists_returns_rank_decayed_scores():
    respx.get("https://api.deezer.com/search/artist").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 7, "name": "Boards of Canada"}]})
    )
    respx.get("https://api.deezer.com/artist/7/related").mock(
        return_value=httpx.Response(
            200, json={"data": [{"name": "Aphex Twin"}, {"name": "Autechre"}]}
        )
    )

    client = DeezerClient()
    candidates = await client.similar_artists("Boards of Canada")

    assert [c.name for c in candidates] == ["Aphex Twin", "Autechre"]
    assert candidates[0].similarity == 1.0
    assert candidates[1].similarity < candidates[0].similarity
    assert candidates[0].sources == ["deezer"]


@respx.mock
async def test_similar_artists_returns_empty_when_artist_not_found():
    respx.get("https://api.deezer.com/search/artist").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    client = DeezerClient()
    candidates = await client.similar_artists("Unknown Artist")

    assert candidates == []
