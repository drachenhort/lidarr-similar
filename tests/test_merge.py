from __future__ import annotations

from lidarr_similar.models import Candidate
from lidarr_similar.pipeline import merge_candidates, normalize_name


def test_merge_combines_disjoint_sources():
    lastfm_candidates = [Candidate(name="A", similarity=0.5, sources=["lastfm"])]
    deezer_candidates = [Candidate(name="B", similarity=0.9, sources=["deezer"])]

    merged = merge_candidates([lastfm_candidates, deezer_candidates])

    assert {c.name for c in merged.values()} == {"A", "B"}
    a = merged[normalize_name("A")]
    b = merged[normalize_name("B")]
    assert a.sources == ["lastfm"]
    assert b.sources == ["deezer"]


def test_merge_boosts_overlap_and_tracks_both_sources():
    lastfm_candidates = [Candidate(name="A", similarity=0.5, sources=["lastfm"])]
    deezer_candidates = [Candidate(name="A", similarity=0.6, sources=["deezer"])]

    merged = merge_candidates([lastfm_candidates, deezer_candidates])
    a = merged[normalize_name("A")]

    assert sorted(a.sources) == ["deezer", "lastfm"]
    assert a.similarity == 0.75  # max(0.5, 0.6) + 0.15 boost


def test_merge_caps_boosted_similarity_at_one():
    lastfm_candidates = [Candidate(name="A", similarity=0.95, sources=["lastfm"])]
    deezer_candidates = [Candidate(name="A", similarity=0.9, sources=["deezer"])]

    merged = merge_candidates([lastfm_candidates, deezer_candidates])

    assert merged[normalize_name("A")].similarity == 1.0


def test_merge_combines_names_differing_only_by_case_and_diacritics():
    lastfm_candidates = [Candidate(name="L'Âme Immortelle", similarity=0.65, sources=["lastfm"])]
    deezer_candidates = [Candidate(name="L'âme Immortelle", similarity=1.0, sources=["deezer"])]

    merged = merge_candidates([lastfm_candidates, deezer_candidates])

    assert len(merged) == 1
    entry = next(iter(merged.values()))
    assert entry.name == "L'Âme Immortelle"  # first-seen spelling kept
    assert sorted(entry.sources) == ["deezer", "lastfm"]
    assert entry.similarity == 1.0  # max(0.65, 1.0) + boost, capped at 1.0
