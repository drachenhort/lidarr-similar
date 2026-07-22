"""Terminal preview: show which artists would be added to Lidarr, without adding them.

Reads Last.fm scrobbles, runs the same discovery pipeline as the full tool
(Last.fm + Deezer candidates, optional Discogs enrichment), and prints a
table. Never calls Lidarr's add-artist endpoint. Lidarr credentials are
optional here — if set, they're used only to exclude artists you already have.
"""

from __future__ import annotations

import argparse
import asyncio

from lidarr_similar.cache import Cache
from lidarr_similar.config import Config
from lidarr_similar.deezer import DeezerClient
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.lastfm import LastFmClient
from lidarr_similar.lidarr import LidarrClient
from lidarr_similar.models import Candidate
from lidarr_similar.pipeline import discover_candidates


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview artists that would be added to Lidarr, based on Last.fm scrobbles."
    )
    parser.add_argument("--limit", type=int, default=25, help="Max candidates to show (default: 25)")
    parser.add_argument(
        "--seed-artists", type=int, default=20, help="Number of top Last.fm artists to seed from"
    )
    parser.add_argument(
        "--similar-per-artist", type=int, default=10, help="Similar artists to fetch per seed"
    )
    parser.add_argument("--no-deezer", action="store_true", help="Disable the Deezer source")
    parser.add_argument("--no-discogs", action="store_true", help="Disable Discogs enrichment")
    parser.add_argument(
        "--no-lidarr", action="store_true", help="Skip Lidarr entirely, even if credentials are set"
    )
    return parser.parse_args(argv)


async def run(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = Config.from_env()
    cache = Cache(config.cache_path)

    lastfm = LastFmClient(config.lastfm_api_key)
    deezer = DeezerClient() if config.deezer_enabled and not args.no_deezer else None
    discogs = (
        DiscogsEnricher(config.discogs_token, cache)
        if config.discogs_enabled and config.discogs_token and not args.no_discogs
        else None
    )
    lidarr = (
        LidarrClient(config.lidarr_url, config.lidarr_api_key)
        if config.lidarr_url and config.lidarr_api_key and not args.no_lidarr
        else None
    )

    try:
        existing = await lidarr.existing_artist_names() if lidarr is not None else set()
        candidates = await discover_candidates(
            lastfm,
            config.lastfm_username,
            discogs,
            existing,
            deezer=deezer,
            top_n_seed_artists=args.seed_artists,
            similar_per_artist=args.similar_per_artist,
        )
        print_table(candidates[: args.limit], dedupe_active=lidarr is not None)
    finally:
        await lastfm.aclose()
        if deezer is not None:
            await deezer.aclose()
        if discogs is not None:
            await discogs.aclose()
        if lidarr is not None:
            await lidarr.aclose()
        cache.close()


def print_table(candidates: list[Candidate], dedupe_active: bool) -> None:
    if not candidates:
        print("No candidates found.")
        return

    name_width = max(len(c.name) for c in candidates)
    name_width = max(name_width, len("Artist"))

    header = f"{'#':>3}  {'Artist':<{name_width}}  {'Score':>5}  {'Sources':<14}  Genres"
    print(header)
    print("-" * len(header))

    for rank, candidate in enumerate(candidates, start=1):
        sources = ",".join(candidate.sources) or "-"
        genres = ", ".join(candidate.discogs_genres) or "-"
        print(
            f"{rank:>3}  {candidate.name:<{name_width}}  {candidate.similarity:>5.2f}  "
            f"{sources:<14}  {genres}"
        )

    print()
    print(f"{len(candidates)} candidate(s) shown.")
    if not dedupe_active:
        print("Note: Lidarr dedupe was skipped, this list may include artists already in your library.")


if __name__ == "__main__":
    asyncio.run(run())
