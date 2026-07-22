from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from lidarr_similar import web
from lidarr_similar.config import Config
from lidarr_similar.models import Candidate
from lidarr_similar.store import CandidateStore, IgnoreList
from lidarr_similar.web import app


@pytest.fixture(autouse=True)
def reset_status():
    web._status.running = False
    web._status.error = None
    yield
    web._status.running = False
    web._status.error = None


def test_index_shows_message_when_store_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "store.sqlite3"))
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "No discovery run yet." in response.text
    assert "No candidates to show." in response.text


def test_index_lists_stored_candidates(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all(
        [
            Candidate(
                name="VNV Nation",
                similarity=0.9,
                sources=["lastfm", "deezer"],
                deezer_genre="Electro",
                discogs_latest_release_year="2025",
            )
        ]
    )
    store.close()

    client = TestClient(app)
    response = client.get("/")

    assert "VNV Nation" in response.text
    assert "Electro" in response.text
    assert "2025" in response.text
    assert "Last updated:" in response.text


def test_index_shows_already_in_library_notice(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all(
        [
            Candidate(name="Known Artist", similarity=0.9, sources=["lastfm"], already_in_library=True),
            Candidate(name="New Artist", similarity=0.8, sources=["lastfm"], already_in_library=False),
        ]
    )
    store.close()

    client = TestClient(app)
    response = client.get("/")

    rows = response.text.split("<tr")
    known_row = next(row for row in rows if "Known Artist" in row)
    new_row = next(row for row in rows if "New Artist" in row)
    assert "already in library" in known_row
    assert "already in library" not in new_row


def test_index_filters_by_min_score(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all(
        [
            Candidate(name="High", similarity=0.9, sources=["lastfm"]),
            Candidate(name="Low", similarity=0.2, sources=["lastfm"]),
        ]
    )
    store.close()

    client = TestClient(app)
    response = client.get("/?min_score=0.5")

    assert "High" in response.text
    assert "Low" not in response.text


def test_index_shows_running_banner_and_disables_button(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "store.sqlite3"))
    web._status.running = True
    client = TestClient(app)

    response = client.get("/")

    assert "Discovery running" in response.text
    assert "disabled" in response.text


def test_index_shows_error_banner_after_failed_run(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "store.sqlite3"))
    web._status.error = "boom"
    client = TestClient(app)

    response = client.get("/")

    assert "Last run failed: boom" in response.text


def test_refresh_without_credentials_sets_error(monkeypatch):
    for var in ("LASTFM_API_KEY", "LASTFM_USERNAME"):
        monkeypatch.delenv(var, raising=False)
    client = TestClient(app)

    response = client.post("/refresh", follow_redirects=False)

    assert response.status_code == 303
    assert web._status.running is False
    assert web._status.error is not None


def test_refresh_sets_running_flag_and_returns_immediately(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")

    async def slow_discovery(config):
        await asyncio.sleep(3600)

    monkeypatch.setattr(web, "_run_discovery", slow_discovery)

    client = TestClient(app)
    response = client.post("/refresh", follow_redirects=False)

    assert response.status_code == 303
    assert web._status.running is True


async def test_run_discovery_persists_candidates_and_clears_running_flag(tmp_path, monkeypatch):
    candidates = [Candidate(name="X", similarity=0.5, sources=["lastfm"])]

    async def fake_discover_candidates(*args, on_progress=None, **kwargs):
        if on_progress is not None:
            await on_progress(candidates)
        return candidates

    monkeypatch.setattr(web, "discover_candidates", fake_discover_candidates)
    config = Config(
        lastfm_api_key="key",
        lastfm_username="user",
        discogs_token=None,
        discogs_enabled=False,
        deezer_enabled=False,
        lidarr_url=None,
        lidarr_api_key=None,
        lidarr_root_folder=None,
        lidarr_quality_profile_id=None,
        cache_path=str(tmp_path / "cache.sqlite3"),
        store_path=str(tmp_path / "store.sqlite3"),
    )
    web._status.running = True

    await web._run_discovery(config)

    assert web._status.running is False
    store = CandidateStore(config.store_path)
    assert [c.name for c in store.load_all()] == ["X"]
    store.close()


def test_index_paginates_results(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all(
        [Candidate(name=f"Artist {i}", similarity=1.0 - i / 1000, sources=["lastfm"]) for i in range(60)]
    )
    store.close()

    client = TestClient(app)
    page1 = client.get("/")
    page2 = client.get("/?page=2")

    assert "Artist 0" in page1.text
    assert "Artist 49" in page1.text
    assert "Artist 50" not in page1.text
    assert "Page 1 of 2" in page1.text

    assert "Artist 50" in page2.text
    assert "Artist 0" not in page2.text
    assert "Page 2 of 2" in page2.text


def test_index_hides_no_pagination_controls_for_single_page(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all([Candidate(name="Solo", similarity=0.9, sources=["lastfm"])])
    store.close()

    client = TestClient(app)
    response = client.get("/")

    assert 'class="pagination"' not in response.text


def test_index_excludes_ignored_artists(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all(
        [
            Candidate(name="Ignored Artist", similarity=0.9, sources=["lastfm"]),
            Candidate(name="Kept Artist", similarity=0.8, sources=["lastfm"]),
        ]
    )
    store.close()
    ignore_list = IgnoreList(store_path)
    ignore_list.add("Ignored Artist")
    ignore_list.close()

    client = TestClient(app)
    response = client.get("/")

    assert "Ignored Artist" not in response.text
    assert "Kept Artist" in response.text


def test_ignore_endpoint_removes_candidate_and_persists(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all([Candidate(name="Unwanted", similarity=0.9, sources=["lastfm"])])
    store.close()

    client = TestClient(app)
    response = client.post("/ignore", data={"name": "Unwanted"}, follow_redirects=False)

    assert response.status_code == 303
    assert "message=" in response.headers["location"]

    ignore_list = IgnoreList(store_path)
    assert ignore_list.is_ignored("Unwanted") is True
    ignore_list.close()

    store = CandidateStore(store_path)
    assert store.load_all() == []
    store.close()


def test_add_endpoint_without_lidarr_config_shows_error(tmp_path, monkeypatch):
    for var in ("LIDARR_URL", "LIDARR_API_KEY", "LIDARR_ROOT_FOLDER", "LIDARR_QUALITY_PROFILE_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")

    client = TestClient(app)
    response = client.post("/add", data={"name": "Some Artist"}, follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_add_endpoint_marks_candidate_in_library_on_success(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")
    monkeypatch.setenv("LIDARR_URL", "http://lidarr.local")
    monkeypatch.setenv("LIDARR_API_KEY", "lidarr-key")
    monkeypatch.setenv("LIDARR_ROOT_FOLDER", "/music")
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "1")

    store = CandidateStore(store_path)
    store.replace_all([Candidate(name="New Band", similarity=0.9, sources=["lastfm"])])
    store.close()

    class FakeLidarrClient:
        def __init__(self, *args, **kwargs):
            pass

        async def lookup_artist(self, name):
            return {"artistName": name, "foreignArtistId": "abc"}

        async def add_artist(self, candidate, root_folder, quality_profile_id):
            return None

        async def aclose(self):
            pass

    monkeypatch.setattr(web, "LidarrClient", FakeLidarrClient)

    client = TestClient(app)
    response = client.post("/add", data={"name": "New Band"}, follow_redirects=False)

    assert response.status_code == 303
    assert "message=" in response.headers["location"]

    store = CandidateStore(store_path)
    candidate = next(c for c in store.load_all() if c.name == "New Band")
    assert candidate.already_in_library is True
    store.close()
