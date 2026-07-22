"""Configuration loaded from environment variables, with UI-editable overrides.

Values saved via the web UI's /config page (SettingsStore, SQLite) take priority
over the environment variable of the same name. This exists because the running
process can't durably change its own environment - editing a value in the browser
has to persist somewhere else, and the same SQLite file already used for discovery
results and ignore lists is the natural place. CACHE_PATH/STORE_PATH are the
exception: they're where SettingsStore itself lives, so they stay environment-only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from lidarr_similar.store import SettingsStore

OVERRIDABLE_KEYS = (
    "LASTFM_API_KEY",
    "LASTFM_USERNAME",
    "DISCOGS_TOKEN",
    "DISCOGS_ENABLED",
    "DEEZER_ENABLED",
    "LISTENBRAINZ_ENABLED",
    "LIDARR_URL",
    "LIDARR_API_KEY",
    "LIDARR_ROOT_FOLDER",
    "LIDARR_QUALITY_PROFILE_ID",
)

SECRET_KEYS = ("LASTFM_API_KEY", "DISCOGS_TOKEN", "LIDARR_API_KEY")


@dataclass(frozen=True)
class Config:
    lastfm_api_key: str
    lastfm_username: str
    discogs_token: str | None
    discogs_enabled: bool
    deezer_enabled: bool
    listenbrainz_enabled: bool
    lidarr_url: str | None
    lidarr_api_key: str | None
    lidarr_root_folder: str | None
    lidarr_quality_profile_id: int | None
    cache_path: str
    store_path: str

    @classmethod
    def from_env(cls) -> "Config":
        overrides = _load_overrides()
        quality_profile_id = _get(overrides, "LIDARR_QUALITY_PROFILE_ID")
        return cls(
            lastfm_api_key=_require(overrides, "LASTFM_API_KEY"),
            lastfm_username=_require(overrides, "LASTFM_USERNAME"),
            discogs_token=_get(overrides, "DISCOGS_TOKEN"),
            discogs_enabled=(_get(overrides, "DISCOGS_ENABLED", "true") or "true").lower() == "true",
            deezer_enabled=(_get(overrides, "DEEZER_ENABLED", "true") or "true").lower() == "true",
            listenbrainz_enabled=(_get(overrides, "LISTENBRAINZ_ENABLED", "true") or "true").lower() == "true",
            lidarr_url=_get(overrides, "LIDARR_URL"),
            lidarr_api_key=_get(overrides, "LIDARR_API_KEY"),
            lidarr_root_folder=_get(overrides, "LIDARR_ROOT_FOLDER"),
            lidarr_quality_profile_id=_parse_int(quality_profile_id),
            cache_path=os.environ.get("CACHE_PATH", "lidarr_similar.sqlite3"),
            store_path=os.environ.get("STORE_PATH", "lidarr_similar_store.sqlite3"),
        )


def _load_overrides() -> dict[str, str]:
    store_path = os.environ.get("STORE_PATH", "lidarr_similar_store.sqlite3")
    settings = SettingsStore(store_path)
    try:
        return settings.get_all()
    finally:
        settings.close()


def _get(overrides: dict[str, str], name: str, default: str | None = None) -> str | None:
    return overrides.get(name) or os.environ.get(name, default)


def get_effective(name: str) -> str | None:
    """A single value as Config.from_env() would see it - UI override if set, else the
    environment variable. For callers (like the /config page) that need one value without
    constructing a full Config, which would raise if LASTFM_API_KEY/USERNAME are missing."""
    return _get(_load_overrides(), name)


def _require(overrides: dict[str, str], name: str) -> str:
    value = _get(overrides, name)
    if not value:
        raise RuntimeError(f"Missing required setting: {name} (set it via /config or as an environment variable)")
    return value


def _parse_int(value: str | None) -> int | None:
    """LIDARR_QUALITY_PROFILE_ID must be the profile's numeric ID, not its display name.
    Treat an unparseable value as unset rather than crashing the whole app."""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class ConfigItem:
    name: str
    present: bool
    required_for: str
    valid: bool = True
    note: str | None = None
    value_preview: str | None = None
    source: str | None = None  # "UI override" or "environment", when present
    editable: bool = False
    secret: bool = False


def describe_config() -> list[ConfigItem]:
    """Presence/validity/source of every config var, for the /config page - never
    returns actual secret values, only whether they're set and (where checkable)
    well-formed."""
    overrides = _load_overrides()

    def source_of(name: str) -> str | None:
        if name in overrides and overrides[name]:
            return "UI override"
        if os.environ.get(name):
            return "environment"
        return None

    quality_profile_id_raw = _get(overrides, "LIDARR_QUALITY_PROFILE_ID")
    quality_profile_valid = _parse_int(quality_profile_id_raw) is not None if quality_profile_id_raw else True

    def item(name: str, required_for: str, **kwargs) -> ConfigItem:
        value = _get(overrides, name)
        return ConfigItem(
            name,
            present=bool(value),
            required_for=required_for,
            source=source_of(name),
            editable=name in OVERRIDABLE_KEYS,
            secret=name in SECRET_KEYS,
            **kwargs,
        )

    return [
        item("LASTFM_API_KEY", "core discovery pipeline"),
        item("LASTFM_USERNAME", "core discovery pipeline", value_preview=_get(overrides, "LASTFM_USERNAME")),
        item("DISCOGS_TOKEN", "Discogs genre/release-year enrichment"),
        item(
            "DISCOGS_ENABLED",
            "toggle for Discogs enrichment",
            value_preview=_get(overrides, "DISCOGS_ENABLED", "true (default)"),
        ),
        item(
            "DEEZER_ENABLED",
            "toggle for Deezer similarity + genre/popularity",
            value_preview=_get(overrides, "DEEZER_ENABLED", "true (default)"),
        ),
        item(
            "LISTENBRAINZ_ENABLED",
            "toggle for ListenBrainz popularity",
            value_preview=_get(overrides, "LISTENBRAINZ_ENABLED", "true (default)"),
        ),
        item("LIDARR_URL", "library dedupe + Add to Lidarr", value_preview=_get(overrides, "LIDARR_URL")),
        item("LIDARR_API_KEY", "library dedupe + Add to Lidarr"),
        item(
            "LIDARR_ROOT_FOLDER", "Add to Lidarr button", value_preview=_get(overrides, "LIDARR_ROOT_FOLDER")
        ),
        item(
            "LIDARR_QUALITY_PROFILE_ID",
            "Add to Lidarr button",
            valid=quality_profile_valid,
            note=None if quality_profile_valid else "set, but not a number - must be the profile's numeric ID, not its name",
            value_preview=quality_profile_id_raw,
        ),
        ConfigItem(
            "CACHE_PATH",
            True,
            "where enrichment lookups are cached",
            value_preview=os.environ.get("CACHE_PATH", "lidarr_similar.sqlite3 (default)"),
            source="environment",
        ),
        ConfigItem(
            "STORE_PATH",
            True,
            "where discovery results + ignore lists persist",
            value_preview=os.environ.get("STORE_PATH", "lidarr_similar_store.sqlite3 (default)"),
            source="environment",
        ),
    ]
