from __future__ import annotations

import httpx
import respx

from lidarr_similar.cache import Cache
from lidarr_similar.listenbrainz import ListenBrainzClient
from lidarr_similar.models import Candidate

MBID = "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"


@respx.mock
async def test_enrich_popularity_sets_listener_count():
    respx.get(f"https://api.listenbrainz.org/1/stats/artist/{MBID}/listeners").mock(
        return_value=httpx.Response(200, json={"payload": {"total_user_count": 24290, "total_listen_count": 5722782}})
    )

    candidate = await ListenBrainzClient().enrich_popularity(Candidate(name="The Beatles", similarity=0.9, mbid=MBID))

    assert candidate.listenbrainz_listeners == 24290


async def test_enrich_popularity_skips_candidate_without_mbid():
    candidate = Candidate(name="No MBID Artist", similarity=0.9, mbid=None)

    result = await ListenBrainzClient().enrich_popularity(candidate)

    assert result is candidate
    assert result.listenbrainz_listeners is None


@respx.mock
async def test_enrich_popularity_handles_204_no_content_without_crashing():
    # Found live: an artist with no ListenBrainz data returns 204 with an empty body,
    # which crashed response.json() with an uncaught JSONDecodeError - not an httpx.HTTPError,
    # so it wasn't caught, and silently aborted the rest of the enrichment loop for every
    # subsequent candidate in a real discovery run (only 15/140 got processed before this fix).
    respx.get(f"https://api.listenbrainz.org/1/stats/artist/{MBID}/listeners").mock(
        return_value=httpx.Response(204)
    )

    candidate = Candidate(name="Obscure Artist", similarity=0.9, mbid=MBID)
    result = await ListenBrainzClient().enrich_popularity(candidate)

    assert result is candidate
    assert result.listenbrainz_listeners is None


@respx.mock
async def test_enrich_popularity_api_error_leaves_candidate_unchanged():
    respx.get(f"https://api.listenbrainz.org/1/stats/artist/{MBID}/listeners").mock(return_value=httpx.Response(500))

    candidate = Candidate(name="The Beatles", similarity=0.9, mbid=MBID)
    result = await ListenBrainzClient().enrich_popularity(candidate)

    assert result is candidate
    assert result.listenbrainz_listeners is None


@respx.mock
async def test_enrich_popularity_uses_cache_on_second_call(tmp_path):
    route = respx.get(f"https://api.listenbrainz.org/1/stats/artist/{MBID}/listeners").mock(
        return_value=httpx.Response(200, json={"payload": {"total_user_count": 24290}})
    )

    cache = Cache(tmp_path / "cache.sqlite3")
    client = ListenBrainzClient(cache)

    await client.enrich_popularity(Candidate(name="The Beatles", similarity=0.9, mbid=MBID))
    second = await client.enrich_popularity(Candidate(name="The Beatles", similarity=0.9, mbid=MBID))

    assert route.call_count == 1
    assert second.listenbrainz_listeners == 24290
