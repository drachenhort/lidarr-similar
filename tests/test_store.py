from __future__ import annotations

from lidarr_similar.models import Candidate
from lidarr_similar.store import CandidateStore, GenreIgnoreList, IgnoreList, SettingsStore


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
                popularity=46417,
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
    assert loaded[0].popularity == 46417
    assert loaded[1].popularity is None
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


def test_ignore_list_list_ordered_most_recent_first(tmp_path):
    ignore_list = IgnoreList(tmp_path / "ignore.sqlite3")

    ignore_list.add("First Ignored")
    ignore_list.add("Second Ignored")
    ignore_list.add("Third Ignored")

    assert ignore_list.list_ordered() == ["Third Ignored", "Second Ignored", "First Ignored"]


def test_genre_ignore_list_matching_is_case_insensitive_substring(tmp_path):
    genre_ignore_list = GenreIgnoreList(tmp_path / "ignore.sqlite3")
    genre_ignore_list.add("Rap")

    assert genre_ignore_list.matching_genre(["Rock"]) is None
    assert genre_ignore_list.matching_genre(["Rap/Hip Hop"]) == "Rap"
    assert genre_ignore_list.matching_genre(["RAP"]) == "Rap"
    assert genre_ignore_list.matching_genre(["Rock", "Gangsta Rap"]) == "Rap"


def test_genre_ignore_list_remove(tmp_path):
    genre_ignore_list = GenreIgnoreList(tmp_path / "ignore.sqlite3")
    genre_ignore_list.add("Rap")

    genre_ignore_list.remove("rap")

    assert genre_ignore_list.matching_genre(["Rap"]) is None
    assert genre_ignore_list.list_ordered() == []


def test_genre_ignore_list_ordered_most_recent_first(tmp_path):
    genre_ignore_list = GenreIgnoreList(tmp_path / "ignore.sqlite3")

    genre_ignore_list.add("Rap")
    genre_ignore_list.add("Country")

    assert genre_ignore_list.list_ordered() == ["Country", "Rap"]


def test_settings_store_roundtrip(tmp_path):
    settings = SettingsStore(tmp_path / "settings.sqlite3")

    assert settings.get("LASTFM_API_KEY") is None

    settings.set("LASTFM_API_KEY", "abc123")

    assert settings.get("LASTFM_API_KEY") == "abc123"
    assert settings.get_all() == {"LASTFM_API_KEY": "abc123"}


def test_settings_store_set_overwrites_existing_value(tmp_path):
    settings = SettingsStore(tmp_path / "settings.sqlite3")
    settings.set("LIDARR_URL", "http://old")

    settings.set("LIDARR_URL", "http://new")

    assert settings.get("LIDARR_URL") == "http://new"


def test_settings_store_clear(tmp_path):
    settings = SettingsStore(tmp_path / "settings.sqlite3")
    settings.set("DISCOGS_TOKEN", "secret")

    settings.clear("DISCOGS_TOKEN")

    assert settings.get("DISCOGS_TOKEN") is None
    assert settings.get_all() == {}
