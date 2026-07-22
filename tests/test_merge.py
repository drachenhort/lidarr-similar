from __future__ import annotations

from lidarr_similar.models import Candidate
from lidarr_similar.pipeline import merge_candidates


def test_merge_combines_disjoint_sources():
    lastfm_candidates = [Candidate(name="A", similarity=0.5, sources=["lastfm"])]
    deezer_candidates = [Candidate(name="B", similarity=0.9, sources=["deezer"])]

    merged = merge_candidates([lastfm_candidates, deezer_candidates])

    assert set(merged) == {"A", "B"}
    assert merged["A"].sources == ["lastfm"]
    assert merged["B"].sources == ["deezer"]


def test_merge_boosts_overlap_and_tracks_both_sources():
    lastfm_candidates = [Candidate(name="A", similarity=0.5, sources=["lastfm"])]
    deezer_candidates = [Candidate(name="A", similarity=0.6, sources=["deezer"])]

    merged = merge_candidates([lastfm_candidates, deezer_candidates])

    assert sorted(merged["A"].sources) == ["deezer", "lastfm"]
    assert merged["A"].similarity == 0.75  # max(0.5, 0.6) + 0.15 boost


def test_merge_caps_boosted_similarity_at_one():
    lastfm_candidates = [Candidate(name="A", similarity=0.95, sources=["lastfm"])]
    deezer_candidates = [Candidate(name="A", similarity=0.9, sources=["deezer"])]

    merged = merge_candidates([lastfm_candidates, deezer_candidates])

    assert merged["A"].similarity == 1.0
