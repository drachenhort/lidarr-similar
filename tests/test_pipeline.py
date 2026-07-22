from __future__ import annotations

from unittest.mock import AsyncMock

from lidarr_similar.models import Candidate
from lidarr_similar.pipeline import discover_candidates


async def test_discover_candidates_dedupes_and_sorts_by_similarity():
    lastfm = AsyncMock()
    lastfm.top_artists.return_value = ["Seed A", "Seed B"]
    lastfm.similar_artists.side_effect = [
        [Candidate(name="X", similarity=0.5), Candidate(name="Y", similarity=0.9)],
        [Candidate(name="X", similarity=0.8), Candidate(name="Z", similarity=0.3)],
    ]

    candidates = await discover_candidates(
        lastfm, username="user", discogs=None, existing_artist_names=set()
    )

    assert [c.name for c in candidates] == ["Y", "X", "Z"]
    assert next(c for c in candidates if c.name == "X").similarity == 0.8


async def test_discover_candidates_skips_existing_lidarr_artists():
    lastfm = AsyncMock()
    lastfm.top_artists.return_value = ["Seed A"]
    lastfm.similar_artists.return_value = [
        Candidate(name="Already Have", similarity=0.9),
        Candidate(name="New Artist", similarity=0.7),
    ]

    candidates = await discover_candidates(
        lastfm, username="user", discogs=None, existing_artist_names={"Already Have"}
    )

    assert [c.name for c in candidates] == ["New Artist"]


async def test_discover_candidates_applies_discogs_enrichment():
    lastfm = AsyncMock()
    lastfm.top_artists.return_value = ["Seed A"]
    lastfm.similar_artists.return_value = [Candidate(name="X", similarity=0.9)]

    discogs = AsyncMock()
    discogs.enrich.side_effect = lambda c: c

    await discover_candidates(lastfm, username="user", discogs=discogs, existing_artist_names=set())

    discogs.enrich.assert_awaited_once()
