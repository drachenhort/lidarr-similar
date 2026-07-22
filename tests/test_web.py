from __future__ import annotations

import asyncio
import os

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from lidarr_similar import web
from lidarr_similar.config import Config, get_effective
from lidarr_similar.models import Candidate
from lidarr_similar.store import CandidateStore, GenreIgnoreList, IgnoreList, SettingsStore
from lidarr_similar.web import app


@pytest.fixture(autouse=True)
def reset_status():
    web._status.running = False
    web._status.error = None
    yield
    web._status.running = False
    web._status.error = None


@pytest.fixture(autouse=True)
def default_store_path(tmp_path, monkeypatch):
    """Config.from_env()/describe_config() now touch a SettingsStore at STORE_PATH even
    for tests that don't care about it - without this, they'd silently create real sqlite
    files in the repo directory instead of a throwaway tmp_path. Tests that need a specific
    STORE_PATH still override it explicitly and take precedence."""
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "default_store.sqlite3"))


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
                popularity=46417,
                listenbrainz_listeners=1234,
            )
        ]
    )
    store.close()

    client = TestClient(app)
    response = client.get("/")

    assert "VNV Nation" in response.text
    assert "Electro" in response.text
    assert "2025" in response.text
    assert "46,417" in response.text
    assert "1,234" in response.text
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
        listenbrainz_enabled=False,
        lidarr_url=None,
        lidarr_api_key=None,
        lidarr_root_folder=None,
        lidarr_quality_profile_id=None,
        lidarr_metadata_profile_id=None,
        cache_path=str(tmp_path / "cache.sqlite3"),
        store_path=str(tmp_path / "store.sqlite3"),
    )
    web._status.running = True

    await web._run_discovery(config)

    assert web._status.running is False
    store = CandidateStore(config.store_path)
    assert [c.name for c in store.load_all()] == ["X"]
    store.close()


async def test_run_discovery_continues_when_lidarr_fails(tmp_path, monkeypatch):
    """Found live: a Lidarr timeout during existing_artist_identifiers() aborted the whole
    run silently (no candidates saved, and the error banner didn't render because
    httpx.ReadTimeout's str() is empty). Lidarr failures must degrade gracefully instead,
    same as Discogs/Deezer/ListenBrainz, and the error message must never be blank."""
    candidates = [Candidate(name="X", similarity=0.5, sources=["lastfm"])]

    async def fake_discover_candidates(*args, on_progress=None, **kwargs):
        if on_progress is not None:
            await on_progress(candidates)
        return candidates

    monkeypatch.setattr(web, "discover_candidates", fake_discover_candidates)

    class FailingLidarrClient:
        def __init__(self, *args, **kwargs):
            pass

        async def existing_artist_identifiers(self):
            raise TimeoutError  # str(TimeoutError()) == "" - the exact bug found live

        async def aclose(self):
            pass

    monkeypatch.setattr(web, "LidarrClient", FailingLidarrClient)

    config = Config(
        lastfm_api_key="key",
        lastfm_username="user",
        discogs_token=None,
        discogs_enabled=False,
        deezer_enabled=False,
        listenbrainz_enabled=False,
        lidarr_url="http://lidarr.local",
        lidarr_api_key="key",
        lidarr_root_folder=None,
        lidarr_quality_profile_id=None,
        lidarr_metadata_profile_id=None,
        cache_path=str(tmp_path / "cache.sqlite3"),
        store_path=str(tmp_path / "store.sqlite3"),
    )
    web._status.running = True

    await web._run_discovery(config)

    assert web._status.running is False
    store = CandidateStore(config.store_path)
    assert [c.name for c in store.load_all()] == ["X"]  # discovery still ran and saved results
    store.close()
    assert web._status.error  # non-empty: falsy error message would hide the banner entirely
    assert "TimeoutError" in web._status.error


def test_describe_never_returns_empty_string_for_blank_exceptions():
    assert web._describe(TimeoutError()) == "TimeoutError"
    assert web._describe(ValueError("bad value")) == "bad value"


async def test_run_discovery_preserves_mid_run_ignore(tmp_path, monkeypatch):
    """A candidate ignored via the UI while a run is in progress must not be un-ignored
    by the next progress snapshot, even though the pipeline computed ignored=False for it
    at the start of the run (before the user clicked Ignore)."""
    candidates = [Candidate(name="X", similarity=0.5, sources=["lastfm"], ignored=False)]

    async def fake_discover_candidates(*args, on_progress=None, **kwargs):
        if on_progress is not None:
            await on_progress(candidates)  # first snapshot: not yet ignored
            store = CandidateStore(config.store_path)
            store.mark_ignored("X", ignored=True)  # simulate a mid-run /ignore click
            store.close()
            await on_progress(candidates)  # second snapshot: pipeline still thinks ignored=False
        return candidates

    monkeypatch.setattr(web, "discover_candidates", fake_discover_candidates)
    config = Config(
        lastfm_api_key="key",
        lastfm_username="user",
        discogs_token=None,
        discogs_enabled=False,
        deezer_enabled=False,
        listenbrainz_enabled=False,
        lidarr_url=None,
        lidarr_api_key=None,
        lidarr_root_folder=None,
        lidarr_quality_profile_id=None,
        lidarr_metadata_profile_id=None,
        cache_path=str(tmp_path / "cache.sqlite3"),
        store_path=str(tmp_path / "store.sqlite3"),
    )

    await web._run_discovery(config)

    store = CandidateStore(config.store_path)
    candidate = next(c for c in store.load_all() if c.name == "X")
    assert candidate.ignored is True
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


def test_index_shows_ignored_artists_with_notice_pushed_to_bottom(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all(
        [
            Candidate(name="Ignored Artist", similarity=0.9, sources=["lastfm"], ignored=True),
            Candidate(name="Kept Artist", similarity=0.5, sources=["lastfm"], ignored=False),
        ]
    )
    store.close()

    client = TestClient(app)
    response = client.get("/")

    assert "Ignored Artist" in response.text
    assert "Kept Artist" in response.text
    rows = response.text.split("<tr")
    ignored_row = next(row for row in rows if "Ignored Artist" in row)
    kept_row = next(row for row in rows if "Kept Artist" in row)
    assert "badge-ignored" in ignored_row
    assert "Unignore" in ignored_row
    assert "badge-ignored" not in kept_row
    # ignored artist is pushed below the kept one despite higher score
    assert response.text.index("Kept Artist") < response.text.index("Ignored Artist")


def test_ignore_endpoint_marks_candidate_and_persists(tmp_path, monkeypatch):
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
    candidate = next(c for c in store.load_all() if c.name == "Unwanted")
    assert candidate.ignored is True
    store.close()


def test_unignore_endpoint_unmarks_candidate_and_persists(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all([Candidate(name="Reconsidered", similarity=0.9, sources=["lastfm"], ignored=True)])
    store.close()
    ignore_list = IgnoreList(store_path)
    ignore_list.add("Reconsidered")
    ignore_list.close()

    client = TestClient(app)
    response = client.post("/unignore", data={"name": "Reconsidered"}, follow_redirects=False)

    assert response.status_code == 303
    assert "message=" in response.headers["location"]

    ignore_list = IgnoreList(store_path)
    assert ignore_list.is_ignored("Reconsidered") is False
    ignore_list.close()

    store = CandidateStore(store_path)
    candidate = next(c for c in store.load_all() if c.name == "Reconsidered")
    assert candidate.ignored is False
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


def test_index_hides_add_button_when_quality_profile_id_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("LIDARR_URL", "http://lidarr.local")
    monkeypatch.setenv("LIDARR_API_KEY", "key")
    monkeypatch.setenv("LIDARR_ROOT_FOLDER", "/music")
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "Standard")  # a name, not the numeric ID - invalid
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all([Candidate(name="New Artist", similarity=0.9, sources=["lastfm"])])
    store.close()

    client = TestClient(app)
    response = client.get("/")

    assert "Add to Lidarr" not in response.text
    assert "Set LIDARR_URL" in response.text  # the config hint should show instead


def test_index_shows_add_button_when_config_saved_via_settings_store(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all([Candidate(name="New Artist", similarity=0.9, sources=["lastfm"])])
    store.close()

    settings = SettingsStore(store_path)
    settings.set("LIDARR_URL", "http://lidarr.local")
    settings.set("LIDARR_API_KEY", "key")
    settings.set("LIDARR_ROOT_FOLDER", "/music")
    settings.set("LIDARR_QUALITY_PROFILE_ID", "3")
    settings.set("LIDARR_METADATA_PROFILE_ID", "1")
    settings.close()

    client = TestClient(app)
    response = client.get("/")

    assert "Add to Lidarr" in response.text


def test_add_endpoint_marks_candidate_in_library_on_success(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")
    monkeypatch.setenv("LIDARR_URL", "http://lidarr.local")
    monkeypatch.setenv("LIDARR_API_KEY", "lidarr-key")
    monkeypatch.setenv("LIDARR_ROOT_FOLDER", "/music")
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "1")
    monkeypatch.setenv("LIDARR_METADATA_PROFILE_ID", "1")

    store = CandidateStore(store_path)
    store.replace_all([Candidate(name="New Band", similarity=0.9, sources=["lastfm"])])
    store.close()

    class FakeLidarrClient:
        def __init__(self, *args, **kwargs):
            pass

        async def lookup_artist(self, name):
            return {"artistName": name, "foreignArtistId": "abc"}

        async def add_artist(self, candidate, root_folder, quality_profile_id, metadata_profile_id):
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


def test_index_shows_ignored_genres_section(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    genre_ignore_list = GenreIgnoreList(store_path)
    genre_ignore_list.add("Rap")
    genre_ignore_list.close()

    client = TestClient(app)
    response = client.get("/")

    assert "Ignored genres (1)" in response.text
    assert "Rap" in response.text
    # populated panels should be expanded by default, not require a click to reveal
    assert '<details class="ignore-list" open>' in response.text


def test_index_shows_empty_ignore_panels_before_anything_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "store.sqlite3"))

    client = TestClient(app)
    response = client.get("/")

    # panels must be discoverable even with nothing ignored yet, not silently absent
    assert "Ignored artists" in response.text
    assert "Ignored genres" in response.text


def test_index_ignored_artists_panel_open_when_populated(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    ignore_list = IgnoreList(store_path)
    ignore_list.add("Peter Maffay")
    ignore_list.close()

    client = TestClient(app)
    response = client.get("/")

    assert "Ignored artists (1)" in response.text
    assert "Peter Maffay" in response.text
    assert '<details class="ignore-list" open>' in response.text


def test_ignore_genre_endpoint_bans_genre_and_flags_matching_candidates(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all(
        [
            Candidate(name="Rapper Artist", similarity=0.9, sources=["lastfm"], deezer_genre="Rap/Hip Hop"),
            Candidate(name="Rock Artist", similarity=0.8, sources=["lastfm"], discogs_genres=["Rock"]),
        ]
    )
    store.close()

    client = TestClient(app)
    response = client.post("/ignore-genre", data={"genre": "Rap"}, follow_redirects=False)

    assert response.status_code == 303
    assert "message=" in response.headers["location"]

    genre_ignore_list = GenreIgnoreList(store_path)
    assert genre_ignore_list.matching_genre(["Rap/Hip Hop"]) == "Rap"
    genre_ignore_list.close()

    store = CandidateStore(store_path)
    candidates = {c.name: c for c in store.load_all()}
    assert candidates["Rapper Artist"].ignored is True
    assert candidates["Rapper Artist"].ignored_genre == "Rap"
    assert candidates["Rock Artist"].ignored is False
    store.close()


def test_unignore_genre_endpoint_restores_matching_candidates(tmp_path, monkeypatch):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORE_PATH", str(store_path))
    store = CandidateStore(store_path)
    store.replace_all(
        [Candidate(name="Rapper Artist", similarity=0.9, sources=["lastfm"], deezer_genre="Rap/Hip Hop")]
    )
    store.close()
    genre_ignore_list = GenreIgnoreList(store_path)
    genre_ignore_list.add("Rap")
    genre_ignore_list.close()
    store = CandidateStore(store_path)
    store.mark_ignored("Rapper Artist", ignored=True, ignored_genre="Rap")
    store.close()

    client = TestClient(app)
    response = client.post("/unignore-genre", data={"genre": "Rap"}, follow_redirects=False)

    assert response.status_code == 303
    assert "message=" in response.headers["location"]

    genre_ignore_list = GenreIgnoreList(store_path)
    assert genre_ignore_list.matching_genre(["Rap/Hip Hop"]) is None
    genre_ignore_list.close()

    store = CandidateStore(store_path)
    candidate = next(c for c in store.load_all() if c.name == "Rapper Artist")
    assert candidate.ignored is False
    assert candidate.ignored_genre is None
    store.close()


async def test_run_discovery_flags_candidates_matching_ignored_genre(tmp_path, monkeypatch):
    candidates = [
        Candidate(name="Rapper", similarity=0.9, sources=["lastfm"], deezer_genre="Rap/Hip Hop"),
        Candidate(name="Rocker", similarity=0.8, sources=["lastfm"], discogs_genres=["Rock"]),
    ]

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
        listenbrainz_enabled=False,
        lidarr_url=None,
        lidarr_api_key=None,
        lidarr_root_folder=None,
        lidarr_quality_profile_id=None,
        lidarr_metadata_profile_id=None,
        cache_path=str(tmp_path / "cache.sqlite3"),
        store_path=str(tmp_path / "store.sqlite3"),
    )
    genre_ignore_list = GenreIgnoreList(config.store_path)
    genre_ignore_list.add("Rap")
    genre_ignore_list.close()

    await web._run_discovery(config)

    store = CandidateStore(config.store_path)
    result = {c.name: c for c in store.load_all()}
    assert result["Rapper"].ignored is True
    assert result["Rapper"].ignored_genre == "Rap"
    assert result["Rocker"].ignored is False
    store.close()


def test_config_page_shows_missing_required_vars(monkeypatch):
    for var in ("LASTFM_API_KEY", "LASTFM_USERNAME"):
        monkeypatch.delenv(var, raising=False)

    client = TestClient(app)
    response = client.get("/config")

    assert response.status_code == 200
    assert "LASTFM_API_KEY" in response.text
    assert "not set" in response.text
    assert "missing required configuration" in response.text


def test_config_page_shows_present_vars_as_set(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")

    client = TestClient(app)
    response = client.get("/config")

    assert response.status_code == 200
    assert "is configured" in response.text


def test_config_page_flags_invalid_quality_profile_id(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "Standard")

    client = TestClient(app)
    response = client.get("/config")

    assert "set, but invalid" in response.text
    assert "numeric ID" in response.text


def test_config_page_never_leaks_secret_values(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "super-secret-key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")
    monkeypatch.setenv("DISCOGS_TOKEN", "another-secret")

    client = TestClient(app)
    response = client.get("/config")

    assert "super-secret-key" not in response.text
    assert "another-secret" not in response.text


def test_index_links_to_config_page(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "store.sqlite3"))

    client = TestClient(app)
    response = client.get("/")

    assert 'href="/config"' in response.text


def test_save_config_persists_editable_fields(monkeypatch):
    monkeypatch.setenv("LASTFM_USERNAME", "user")

    client = TestClient(app)
    response = client.post(
        "/config",
        data={"LASTFM_API_KEY": "new-key", "LIDARR_URL": "http://lidarr.local", "LIDARR_ROOT_FOLDER": "/music"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "message=" in response.headers["location"]
    assert get_effective("LASTFM_API_KEY") == "new-key"
    assert get_effective("LIDARR_URL") == "http://lidarr.local"
    assert get_effective("LIDARR_ROOT_FOLDER") == "/music"


def test_save_config_blank_field_does_not_overwrite_existing_value():
    settings = SettingsStore(os.environ["STORE_PATH"])
    settings.set("DISCOGS_TOKEN", "existing-token")
    settings.close()

    client = TestClient(app)
    client.post("/config", data={"DISCOGS_TOKEN": ""}, follow_redirects=False)

    assert get_effective("DISCOGS_TOKEN") == "existing-token"


def test_save_config_ignores_non_overridable_keys():
    client = TestClient(app)
    client.post("/config", data={"CACHE_PATH": "/hacked", "STORE_PATH": "/hacked"}, follow_redirects=False)

    settings = SettingsStore(os.environ["STORE_PATH"])
    assert settings.get_all() == {}
    settings.close()


def test_config_page_shows_editable_inputs_for_overridable_fields(monkeypatch):
    monkeypatch.setenv("LASTFM_USERNAME", "myuser")

    client = TestClient(app)
    response = client.get("/config")

    assert 'name="LASTFM_USERNAME"' in response.text
    assert 'value="myuser"' in response.text
    assert 'name="LASTFM_API_KEY"' in response.text
    assert 'type="password"' in response.text


@respx.mock
def test_config_page_shows_quality_profile_dropdown_when_lidarr_reachable(monkeypatch):
    monkeypatch.setenv("LIDARR_URL", "http://lidarr.local")
    monkeypatch.setenv("LIDARR_API_KEY", "key")
    respx.get("http://lidarr.local/api/v1/qualityprofile").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Any"}, {"id": 3, "name": "Standard"}])
    )
    respx.get("http://lidarr.local/api/v1/metadataprofile").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Standard"}])
    )

    client = TestClient(app)
    response = client.get("/config")

    assert "<select" in response.text
    assert "Standard (id 3)" in response.text


@respx.mock
def test_config_page_shows_metadata_profile_dropdown_when_lidarr_reachable(monkeypatch):
    monkeypatch.setenv("LIDARR_URL", "http://lidarr.local")
    monkeypatch.setenv("LIDARR_API_KEY", "key")
    respx.get("http://lidarr.local/api/v1/qualityprofile").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Any"}])
    )
    respx.get("http://lidarr.local/api/v1/metadataprofile").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Standard"}, {"id": 2, "name": "None"}])
    )

    client = TestClient(app)
    response = client.get("/config")

    rows = response.text.split("<tr>")
    metadata_row = next(r for r in rows if "LIDARR_METADATA_PROFILE_ID" in r)
    assert "<select" in metadata_row
    assert "None (id 2)" in metadata_row


def test_config_page_falls_back_to_text_input_when_lidarr_unreachable(monkeypatch):
    monkeypatch.delenv("LIDARR_URL", raising=False)
    monkeypatch.delenv("LIDARR_API_KEY", raising=False)

    client = TestClient(app)
    response = client.get("/config")

    rows = response.text.split("<tr>")
    profile_row = next(r for r in rows if "LIDARR_QUALITY_PROFILE_ID" in r)
    assert 'name="LIDARR_QUALITY_PROFILE_ID"' in profile_row
    assert "<select" not in profile_row


@respx.mock
def test_test_lidarr_connection_reports_success(monkeypatch):
    respx.get("http://lidarr.local/api/v1/system/status").mock(
        return_value=httpx.Response(200, json={"version": "1.2.3.4"})
    )

    client = TestClient(app)
    response = client.post(
        "/config/test-lidarr",
        data={"LIDARR_URL": "http://lidarr.local", "LIDARR_API_KEY": "key"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Connected to Lidarr 1.2.3.4" in response.text


@respx.mock
def test_test_lidarr_connection_reports_failure(monkeypatch):
    respx.get("http://lidarr.local/api/v1/system/status").mock(return_value=httpx.Response(401))

    client = TestClient(app)
    response = client.post(
        "/config/test-lidarr",
        data={"LIDARR_URL": "http://lidarr.local", "LIDARR_API_KEY": "wrong-key"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Could not connect to Lidarr" in response.text


def test_test_lidarr_connection_requires_url_and_key():
    client = TestClient(app)
    response = client.post("/config/test-lidarr", data={}, follow_redirects=True)

    assert response.status_code == 200
    assert "Set LIDARR_URL and LIDARR_API_KEY" in response.text


@respx.mock
def test_test_lidarr_connection_falls_back_to_saved_api_key_when_field_left_blank(monkeypatch):
    monkeypatch.setenv("LIDARR_API_KEY", "saved-key")
    respx.get("http://lidarr.local/api/v1/system/status").mock(
        return_value=httpx.Response(200, json={"version": "1.2.3.4"})
    )

    client = TestClient(app)
    response = client.post(
        "/config/test-lidarr",
        data={"LIDARR_URL": "http://lidarr.local", "LIDARR_API_KEY": ""},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Connected to Lidarr 1.2.3.4" in response.text
