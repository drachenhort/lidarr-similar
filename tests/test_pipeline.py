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


async def test_discover_candidates_flags_existing_lidarr_artists_instead_of_dropping():
    lastfm = AsyncMock()
    lastfm.top_artists.return_value = ["Seed A"]
    lastfm.similar_artists.return_value = [
        Candidate(name="Already Have", similarity=0.9),
        Candidate(name="New Artist", similarity=0.7),
    ]

    candidates = await discover_candidates(
        lastfm, username="user", discogs=None, existing_artist_names={"Already Have"}
    )

    assert {c.name: c.already_in_library for c in candidates} == {
        "Already Have": True,
        "New Artist": False,
    }


async def test_discover_candidates_flags_existing_artists_ignoring_case_and_diacritics():
    lastfm = AsyncMock()
    lastfm.top_artists.return_value = ["Seed A"]
    lastfm.similar_artists.return_value = [
        Candidate(name="l'âme immortelle", similarity=0.9),
        Candidate(name="New Artist", similarity=0.7),
    ]

    candidates = await discover_candidates(
        lastfm, username="user", discogs=None, existing_artist_names={"L'Âme Immortelle"}
    )

    flagged = next(c for c in candidates if c.name == "l'âme immortelle")
    unflagged = next(c for c in candidates if c.name == "New Artist")
    assert flagged.already_in_library is True
    assert unflagged.already_in_library is False


async def test_discover_candidates_applies_discogs_enrichment():
    lastfm = AsyncMock()
    lastfm.top_artists.return_value = ["Seed A"]
    lastfm.similar_artists.return_value = [Candidate(name="X", similarity=0.9)]

    discogs = AsyncMock()
    discogs.enrich.side_effect = lambda c: c

    await discover_candidates(lastfm, username="user", discogs=discogs, existing_artist_names=set())

    discogs.enrich.assert_awaited_once()


async def test_discover_candidates_drops_ignored_names_before_enrichment():
    lastfm = AsyncMock()
    lastfm.top_artists.return_value = ["Seed A"]
    lastfm.similar_artists.return_value = [
        Candidate(name="Skip Me", similarity=0.9),
        Candidate(name="Keep Me", similarity=0.7),
    ]
    discogs = AsyncMock()
    discogs.enrich.side_effect = lambda c: c

    candidates = await discover_candidates(
        lastfm,
        username="user",
        discogs=discogs,
        existing_artist_names=set(),
        ignored_names={"skip me"},
    )

    assert [c.name for c in candidates] == ["Keep Me"]
    discogs.enrich.assert_awaited_once()


async def test_discover_candidates_calls_on_progress_after_merge_and_each_enrichment():
    lastfm = AsyncMock()
    lastfm.top_artists.return_value = ["Seed A"]
    lastfm.similar_artists.return_value = [
        Candidate(name="X", similarity=0.9),
        Candidate(name="Y", similarity=0.7),
    ]
    discogs = AsyncMock()
    discogs.enrich.side_effect = lambda c: c

    snapshots: list[int] = []

    async def on_progress(candidates):
        snapshots.append(len(candidates))

    await discover_candidates(
        lastfm,
        username="user",
        discogs=discogs,
        existing_artist_names=set(),
        on_progress=on_progress,
    )

    # once after merge, once per candidate enriched (2 candidates)
    assert snapshots == [2, 2, 2]
