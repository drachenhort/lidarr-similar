"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


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
        quality_profile_id = os.environ.get("LIDARR_QUALITY_PROFILE_ID")
        return cls(
            lastfm_api_key=_require("LASTFM_API_KEY"),
            lastfm_username=_require("LASTFM_USERNAME"),
            discogs_token=os.environ.get("DISCOGS_TOKEN"),
            discogs_enabled=os.environ.get("DISCOGS_ENABLED", "true").lower() == "true",
            deezer_enabled=os.environ.get("DEEZER_ENABLED", "true").lower() == "true",
            listenbrainz_enabled=os.environ.get("LISTENBRAINZ_ENABLED", "true").lower() == "true",
            lidarr_url=os.environ.get("LIDARR_URL"),
            lidarr_api_key=os.environ.get("LIDARR_API_KEY"),
            lidarr_root_folder=os.environ.get("LIDARR_ROOT_FOLDER"),
            lidarr_quality_profile_id=_parse_int(quality_profile_id),
            cache_path=os.environ.get("CACHE_PATH", "lidarr_similar.sqlite3"),
            store_path=os.environ.get("STORE_PATH", "lidarr_similar_store.sqlite3"),
        )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
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
