from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from lidarr_similar import web
from lidarr_similar.config import Config
from lidarr_similar.models import Candidate
from lidarr_similar.store import CandidateStore
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
        [Candidate(name="VNV Nation", similarity=0.9, sources=["lastfm", "deezer"], deezer_genre="Electro")]
    )
    store.close()

    client = TestClient(app)
    response = client.get("/")

    assert "VNV Nation" in response.text
    assert "Electro" in response.text
    assert "Last updated:" in response.text


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


def test_index_shows_running_banner_and_disables_button():
    web._status.running = True
    client = TestClient(app)

    response = client.get("/")

    assert "Discovery running" in response.text
    assert "disabled" in response.text


def test_index_shows_error_banner_after_failed_run():
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
    monkeypatch.setattr(
        web, "discover_candidates", lambda *a, **k: _immediate([Candidate(name="X", similarity=0.5, sources=["lastfm"])])
    )
    config = Config(
        lastfm_api_key="key",
        lastfm_username="user",
        discogs_token=None,
        discogs_enabled=False,
        deezer_enabled=False,
        lidarr_url=None,
        lidarr_api_key=None,
        cache_path=str(tmp_path / "cache.sqlite3"),
        store_path=str(tmp_path / "store.sqlite3"),
    )
    web._status.running = True

    await web._run_discovery(config)

    assert web._status.running is False
    store = CandidateStore(config.store_path)
    assert [c.name for c in store.load_all()] == ["X"]
    store.close()


async def _immediate(value):
    return value
