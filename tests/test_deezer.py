from __future__ import annotations

import httpx
import respx

from lidarr_similar.cache import Cache
from lidarr_similar.deezer import DeezerClient
from lidarr_similar.models import Candidate


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


@respx.mock
async def test_enrich_genre_resolves_via_top_album():
    respx.get("https://api.deezer.com/search/artist").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 3821, "name": "VNV Nation", "nb_fan": 46417}]})
    )
    respx.get("https://api.deezer.com/artist/3821/albums").mock(
        return_value=httpx.Response(200, json={"data": [{"genre_id": 106}]})
    )
    respx.get("https://api.deezer.com/genre/106").mock(
        return_value=httpx.Response(200, json={"id": 106, "name": "Electro"})
    )

    candidate = await DeezerClient().enrich_genre(Candidate(name="VNV Nation", similarity=0.9))

    assert candidate.deezer_genre == "Electro"
    assert candidate.popularity == 46417


@respx.mock
async def test_enrich_genre_sets_popularity_even_when_genre_lookup_fails():
    respx.get("https://api.deezer.com/search/artist").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 3821, "name": "VNV Nation", "nb_fan": 46417}]})
    )
    respx.get("https://api.deezer.com/artist/3821/albums").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    candidate = await DeezerClient().enrich_genre(Candidate(name="VNV Nation", similarity=0.9))

    assert candidate.deezer_genre is None
    assert candidate.popularity == 46417


@respx.mock
async def test_enrich_genre_no_match_leaves_candidate_unchanged():
    respx.get("https://api.deezer.com/search/artist").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    candidate = Candidate(name="Unknown Artist", similarity=0.5)
    result = await DeezerClient().enrich_genre(candidate)

    assert result is candidate
    assert result.deezer_genre is None


@respx.mock
async def test_enrich_genre_api_error_leaves_candidate_unchanged():
    respx.get("https://api.deezer.com/search/artist").mock(return_value=httpx.Response(500))

    candidate = Candidate(name="VNV Nation", similarity=0.9)
    result = await DeezerClient().enrich_genre(candidate)

    assert result is candidate
    assert result.deezer_genre is None


@respx.mock
async def test_enrich_genre_uses_cache_on_second_call(tmp_path):
    search_route = respx.get("https://api.deezer.com/search/artist").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 3821, "name": "VNV Nation", "nb_fan": 46417}]})
    )
    respx.get("https://api.deezer.com/artist/3821/albums").mock(
        return_value=httpx.Response(200, json={"data": [{"genre_id": 106}]})
    )
    respx.get("https://api.deezer.com/genre/106").mock(
        return_value=httpx.Response(200, json={"id": 106, "name": "Electro"})
    )

    cache = Cache(tmp_path / "cache.sqlite3")
    client = DeezerClient(cache)

    await client.enrich_genre(Candidate(name="VNV Nation", similarity=0.9))
    second = await client.enrich_genre(Candidate(name="VNV Nation", similarity=0.9))

    assert search_route.call_count == 1
    assert second.deezer_genre == "Electro"
    assert second.popularity == 46417
