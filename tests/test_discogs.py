from __future__ import annotations

import httpx
import pytest
import respx

from lidarr_similar.cache import Cache
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.models import Candidate

SEARCH_URL = "https://api.discogs.com/database/search"


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "cache.sqlite3")


@pytest.fixture
def enricher(cache: Cache) -> DiscogsEnricher:
    return DiscogsEnricher(token="test-token", cache=cache)


def mock_artist_search(artist_id: int = 42, title: str = "Boards of Canada"):
    return respx.get(SEARCH_URL, params={"type": "artist"}).mock(
        return_value=httpx.Response(200, json={"results": [{"id": artist_id, "title": title}]})
    )


def mock_release_search(genres: list[str], styles: list[str], year: str | None = None):
    return respx.get(SEARCH_URL, params={"type": "release"}).mock(
        return_value=httpx.Response(
            200, json={"results": [{"genre": genres, "style": styles, "year": year}]}
        )
    )


@respx.mock
async def test_enrich_exact_match(enricher: DiscogsEnricher):
    mock_artist_search()
    mock_release_search(genres=["Electronic"], styles=["IDM"], year="2025")

    candidate = await enricher.enrich(Candidate(name="Boards of Canada", similarity=0.9))

    assert candidate.discogs_id == 42
    assert candidate.discogs_genres == ["Electronic"]
    assert candidate.discogs_styles == ["IDM"]
    assert candidate.discogs_latest_release_year == "2025"


@respx.mock
async def test_enrich_no_match_leaves_candidate_unchanged(enricher: DiscogsEnricher):
    respx.get(SEARCH_URL, params={"type": "artist"}).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get(SEARCH_URL, params={"type": "release"}).mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    candidate = Candidate(name="Unknown Artist", similarity=0.5)
    result = await enricher.enrich(candidate)

    assert result is candidate
    assert result.discogs_id is None


@respx.mock
async def test_enrich_api_error_leaves_candidate_unchanged(enricher: DiscogsEnricher):
    respx.get(SEARCH_URL, params={"type": "artist"}).mock(return_value=httpx.Response(500))

    candidate = Candidate(name="Boards of Canada", similarity=0.9)
    result = await enricher.enrich(candidate)

    assert result is candidate
    assert result.discogs_id is None


@respx.mock
async def test_enrich_uses_cache_on_second_call(enricher: DiscogsEnricher):
    artist_route = mock_artist_search()
    mock_release_search(genres=["Electronic"], styles=["IDM"])

    await enricher.enrich(Candidate(name="Boards of Canada", similarity=0.9))
    await enricher.enrich(Candidate(name="Boards of Canada", similarity=0.9))

    assert artist_route.call_count == 1
