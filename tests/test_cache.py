from __future__ import annotations

from lidarr_similar.cache import Cache


def test_cache_roundtrip(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3")

    assert cache.get("discogs", "missing") is None

    cache.set("discogs", "Boards of Canada", {"genres": ["Electronic"]})
    assert cache.get("discogs", "Boards of Canada") == {"genres": ["Electronic"]}

    cache.close()
