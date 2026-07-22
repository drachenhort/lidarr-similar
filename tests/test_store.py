from __future__ import annotations

from lidarr_similar.models import Candidate
from lidarr_similar.store import CandidateStore


def test_store_starts_empty(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")

    assert store.load_all() == []
    assert store.last_updated() is None


def test_store_replace_all_persists_and_sorts_by_similarity(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")
    store.replace_all(
        [
            Candidate(name="Low", similarity=0.3, sources=["lastfm"]),
            Candidate(name="High", similarity=0.9, sources=["lastfm", "deezer"], discogs_genres=["Electronic"]),
        ]
    )

    loaded = store.load_all()

    assert [c.name for c in loaded] == ["High", "Low"]
    assert loaded[0].discogs_genres == ["Electronic"]
    assert loaded[0].sources == ["lastfm", "deezer"]
    assert store.last_updated() is not None


def test_store_replace_all_overwrites_previous_snapshot(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")
    store.replace_all([Candidate(name="Old", similarity=0.5, sources=["lastfm"])])
    store.replace_all([Candidate(name="New", similarity=0.5, sources=["lastfm"])])

    assert [c.name for c in store.load_all()] == ["New"]
