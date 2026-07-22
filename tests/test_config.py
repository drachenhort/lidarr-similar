from __future__ import annotations

import os

import pytest

from lidarr_similar.config import Config, describe_config, get_effective
from lidarr_similar.store import SettingsStore


@pytest.fixture(autouse=True)
def base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_USERNAME", "user")
    monkeypatch.setenv("STORE_PATH", str(tmp_path / "store.sqlite3"))


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


def test_describe_config_flags_missing_required_vars(monkeypatch):
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)

    items = {item.name: item for item in describe_config()}

    assert items["LASTFM_API_KEY"].present is False
    assert items["LASTFM_USERNAME"].present is True


def test_describe_config_flags_invalid_quality_profile_id(monkeypatch):
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "Standard")

    items = {item.name: item for item in describe_config()}

    profile_item = items["LIDARR_QUALITY_PROFILE_ID"]
    assert profile_item.present is True
    assert profile_item.valid is False
    assert "numeric ID" in profile_item.note


def test_describe_config_never_exposes_secret_values(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "super-secret-key")
    monkeypatch.setenv("DISCOGS_TOKEN", "another-secret")
    monkeypatch.setenv("LIDARR_API_KEY", "lidarr-secret")

    items = describe_config()

    rendered = str(items)
    assert "super-secret-key" not in rendered
    assert "another-secret" not in rendered
    assert "lidarr-secret" not in rendered


def test_describe_config_valid_numeric_quality_profile_id(monkeypatch):
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "4")

    items = {item.name: item for item in describe_config()}

    assert items["LIDARR_QUALITY_PROFILE_ID"].valid is True


def test_settings_override_takes_priority_over_env_var(monkeypatch):
    monkeypatch.setenv("LIDARR_URL", "http://from-env")
    settings = SettingsStore(os.environ["STORE_PATH"])
    settings.set("LIDARR_URL", "http://from-ui")
    settings.close()

    config = Config.from_env()

    assert config.lidarr_url == "http://from-ui"


def test_settings_override_can_satisfy_required_fields_without_env_var(monkeypatch):
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    monkeypatch.delenv("LASTFM_USERNAME", raising=False)

    settings = SettingsStore(os.environ["STORE_PATH"])
    settings.set("LASTFM_API_KEY", "ui-key")
    settings.set("LASTFM_USERNAME", "ui-user")
    settings.close()

    config = Config.from_env()

    assert config.lastfm_api_key == "ui-key"
    assert config.lastfm_username == "ui-user"


def test_describe_config_reports_source(monkeypatch):
    monkeypatch.setenv("LIDARR_URL", "http://from-env")

    settings = SettingsStore(os.environ["STORE_PATH"])
    settings.set("LIDARR_ROOT_FOLDER", "/music")
    settings.close()

    items = {item.name: item for item in describe_config()}

    assert items["LIDARR_URL"].source == "environment"
    assert items["LIDARR_ROOT_FOLDER"].source == "UI override"
    assert items["LIDARR_API_KEY"].source is None


def test_get_effective_prefers_override_over_env(monkeypatch):
    monkeypatch.setenv("LIDARR_URL", "http://from-env")
    settings = SettingsStore(os.environ["STORE_PATH"])
    settings.set("LIDARR_URL", "http://from-ui")
    settings.close()

    assert get_effective("LIDARR_URL") == "http://from-ui"


def test_get_effective_returns_none_when_unset():
    assert get_effective("LIDARR_URL") is None
