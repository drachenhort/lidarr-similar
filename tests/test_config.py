from __future__ import annotations

import pytest

from lidarr_similar.config import Config


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")


def test_from_env_missing_required_var_raises(monkeypatch):
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        Config.from_env()


def test_from_env_parses_numeric_quality_profile_id(monkeypatch):
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "4")

    config = Config.from_env()

    assert config.lidarr_quality_profile_id == 4


def test_from_env_treats_non_numeric_quality_profile_id_as_unset(monkeypatch):
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "Standard")

    config = Config.from_env()

    assert config.lidarr_quality_profile_id is None


def test_from_env_quality_profile_id_defaults_to_none(monkeypatch):
    monkeypatch.delenv("LIDARR_QUALITY_PROFILE_ID", raising=False)

    config = Config.from_env()

    assert config.lidarr_quality_profile_id is None
