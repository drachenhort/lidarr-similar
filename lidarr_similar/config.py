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
    lidarr_url: str
    lidarr_api_key: str
    cache_path: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            lastfm_api_key=_require("LASTFM_API_KEY"),
            lastfm_username=_require("LASTFM_USERNAME"),
            discogs_token=os.environ.get("DISCOGS_TOKEN"),
            discogs_enabled=os.environ.get("DISCOGS_ENABLED", "true").lower() == "true",
            deezer_enabled=os.environ.get("DEEZER_ENABLED", "true").lower() == "true",
            lidarr_url=_require("LIDARR_URL"),
            lidarr_api_key=_require("LIDARR_API_KEY"),
            cache_path=os.environ.get("CACHE_PATH", "lidarr_similar.sqlite3"),
        )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
