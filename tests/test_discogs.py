from __future__ import annotations

import httpx
import pytest
import respx

from lidarr_similar.cache import Cache
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.models import Candidate


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "cache.sqlite3")


@pytest.fixture
def enricher(cache: Cache) -> DiscogsEnricher:
    return DiscogsEnricher(token="test-token", cache=cache)


@respx.mock
async def test_enrich_exact_match(enricher: DiscogsEnricher):
    respx.get("https://api.discogs.com/database/search").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 42, "title": "Boards of Canada"}]})
    )
    respx.get("https://api.discogs.com/artists/42").mock(
        return_value=httpx.Response(200, json={"genres": ["Electronic"], "styles": ["IDM"]})
    )

    candidate = await enricher.enrich(Candidate(name="Boards of Canada", similarity=0.9))

    assert candidate.discogs_id == 42
    assert candidate.discogs_genres == ["Electronic"]
    assert candidate.discogs_styles == ["IDM"]


@respx.mock
async def test_enrich_no_match_leaves_candidate_unchanged(enricher: DiscogsEnricher):
    respx.get("https://api.discogs.com/database/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    candidate = Candidate(name="Unknown Artist", similarity=0.5)
    result = await enricher.enrich(candidate)

    assert result is candidate
    assert result.discogs_id is None


@respx.mock
async def test_enrich_api_error_leaves_candidate_unchanged(enricher: DiscogsEnricher):
    respx.get("https://api.discogs.com/database/search").mock(return_value=httpx.Response(500))

    candidate = Candidate(name="Boards of Canada", similarity=0.9)
    result = await enricher.enrich(candidate)

    assert result is candidate
    assert result.discogs_id is None


@respx.mock
async def test_enrich_uses_cache_on_second_call(enricher: DiscogsEnricher):
    search_route = respx.get("https://api.discogs.com/database/search").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 42, "title": "Boards of Canada"}]})
    )
    respx.get("https://api.discogs.com/artists/42").mock(
        return_value=httpx.Response(200, json={"genres": ["Electronic"], "styles": ["IDM"]})
    )

    await enricher.enrich(Candidate(name="Boards of Canada", similarity=0.9))
    await enricher.enrich(Candidate(name="Boards of Canada", similarity=0.9))

    assert search_route.call_count == 1
