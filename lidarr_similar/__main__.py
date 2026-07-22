"""CLI entrypoint: discover similar artists and add them to Lidarr."""

from __future__ import annotations

import asyncio

from lidarr_similar.cache import Cache
from lidarr_similar.config import Config
from lidarr_similar.deezer import DeezerClient
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.lastfm import LastFmClient
from lidarr_similar.lidarr import LidarrClient
from lidarr_similar.pipeline import discover_candidates


async def run() -> None:
    config = Config.from_env()
    if not config.lidarr_url or not config.lidarr_api_key:
        raise RuntimeError("LIDARR_URL and LIDARR_API_KEY are required to run the full pipeline")
    cache = Cache(config.cache_path)

    lastfm = LastFmClient(config.lastfm_api_key)
    lidarr = LidarrClient(config.lidarr_url, config.lidarr_api_key)
    deezer = DeezerClient() if config.deezer_enabled else None
    discogs = (
        DiscogsEnricher(config.discogs_token, cache)
        if config.discogs_enabled and config.discogs_token
        else None
    )

    try:
        existing = await lidarr.existing_artist_names()
        candidates = await discover_candidates(
            lastfm, config.lastfm_username, discogs, existing, deezer=deezer
        )
        for candidate in candidates:
            print(f"{candidate.name} ({candidate.similarity:.2f}) {candidate.sources} {candidate.discogs_genres}")
    finally:
        await lastfm.aclose()
        await lidarr.aclose()
        if deezer is not None:
            await deezer.aclose()
        if discogs is not None:
            await discogs.aclose()
        cache.close()


if __name__ == "__main__":
    asyncio.run(run())
