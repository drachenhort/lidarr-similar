from __future__ import annotations

from lidarr_similar.models import Candidate
from lidarr_similar.store import CandidateStore, IgnoreList


def test_store_starts_empty(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")

    assert store.load_all() == []
    assert store.last_updated() is None


def test_store_replace_all_persists_and_sorts_by_similarity(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")
    store.replace_all(
        [
            Candidate(name="Low", similarity=0.3, sources=["lastfm"]),
            Candidate(
                name="High",
                similarity=0.9,
                sources=["lastfm", "deezer"],
                discogs_genres=["Electronic"],
                discogs_latest_release_year="2024",
                already_in_library=True,
            ),
        ]
    )

    loaded = store.load_all()

    assert [c.name for c in loaded] == ["High", "Low"]
    assert loaded[0].discogs_genres == ["Electronic"]
    assert loaded[0].sources == ["lastfm", "deezer"]
    assert loaded[0].discogs_latest_release_year == "2024"
    assert loaded[0].already_in_library is True
    assert loaded[1].already_in_library is False
    assert store.last_updated() is not None


def test_store_replace_all_overwrites_previous_snapshot(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")
    store.replace_all([Candidate(name="Old", similarity=0.5, sources=["lastfm"])])
    store.replace_all([Candidate(name="New", similarity=0.5, sources=["lastfm"])])

    assert [c.name for c in store.load_all()] == ["New"]


def test_store_mark_in_library_flags_a_single_candidate(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")
    store.replace_all(
        [
            Candidate(name="A", similarity=0.5, sources=["lastfm"]),
            Candidate(name="B", similarity=0.4, sources=["lastfm"]),
        ]
    )

    store.mark_in_library("A")

    loaded = {c.name: c.already_in_library for c in store.load_all()}
    assert loaded == {"A": True, "B": False}


def test_store_remove_drops_a_single_candidate(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")
    store.replace_all(
        [
            Candidate(name="A", similarity=0.5, sources=["lastfm"]),
            Candidate(name="B", similarity=0.4, sources=["lastfm"]),
        ]
    )

    store.remove("A")

    assert [c.name for c in store.load_all()] == ["B"]


def test_store_mark_ignored_flags_and_unflags_a_single_candidate(tmp_path):
    store = CandidateStore(tmp_path / "store.sqlite3")
    store.replace_all(
        [
            Candidate(name="A", similarity=0.5, sources=["lastfm"]),
            Candidate(name="B", similarity=0.4, sources=["lastfm"]),
        ]
    )

    store.mark_ignored("A")
    loaded = {c.name: c.ignored for c in store.load_all()}
    assert loaded == {"A": True, "B": False}

    store.mark_ignored("A", ignored=False)
    loaded = {c.name: c.ignored for c in store.load_all()}
    assert loaded == {"A": False, "B": False}


def test_ignore_list_add_and_check(tmp_path):
    ignore_list = IgnoreList(tmp_path / "ignore.sqlite3")

    assert ignore_list.is_ignored("Boards of Canada") is False

    ignore_list.add("Boards of Canada")

    assert ignore_list.is_ignored("Boards of Canada") is True
    assert ignore_list.is_ignored("boards OF canada") is True  # case-insensitive
    assert ignore_list.names() == {"Boards of Canada"}


def test_ignore_list_remove(tmp_path):
    ignore_list = IgnoreList(tmp_path / "ignore.sqlite3")
    ignore_list.add("Boards of Canada")

    ignore_list.remove("boards of canada")

    assert ignore_list.is_ignored("Boards of Canada") is False
    assert ignore_list.names() == set()
