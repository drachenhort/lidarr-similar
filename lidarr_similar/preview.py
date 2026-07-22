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
        "--min-score", type=float, default=0.0, help="Drop candidates below this similarity score (default: 0.0)"
    )
    parser.add_argument(
        "--no-min-score", action="store_true", help="Reset --min-score back to 0.0, showing every candidate"
    )
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
    deezer = DeezerClient(cache) if config.deezer_enabled and not args.no_deezer else None
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
        existing_names, existing_mbids = (
            await lidarr.existing_artist_identifiers() if lidarr is not None else (set(), set())
        )
        candidates = await discover_candidates(
            lastfm,
            config.lastfm_username,
            discogs,
            existing_names,
            deezer=deezer,
            existing_artist_mbids=existing_mbids,
            top_n_seed_artists=args.seed_artists,
            similar_per_artist=args.similar_per_artist,
        )
        min_score = 0.0 if args.no_min_score else args.min_score
        candidates = filter_by_min_score(candidates, min_score)
        print_table(candidates[: args.limit], library_check_active=lidarr is not None)
    finally:
        await lastfm.aclose()
        if deezer is not None:
            await deezer.aclose()
        if discogs is not None:
            await discogs.aclose()
        if lidarr is not None:
            await lidarr.aclose()
        cache.close()


def filter_by_min_score(candidates: list[Candidate], min_score: float) -> list[Candidate]:
    return [c for c in candidates if c.similarity >= min_score]


def print_table(candidates: list[Candidate], library_check_active: bool) -> None:
    if not candidates:
        print("No candidates found.")
        return

    name_width = max(len(c.name) for c in candidates)
    name_width = max(name_width, len("Artist"))

    header = (
        f"{'#':>3}  {'Artist':<{name_width}}  {'Score':>5}  {'Sources':<14}  {'Popularity':>10}  "
        f"{'Last Release':<12}  {'In Library':<10}  Genres"
    )
    print(header)
    print("-" * len(header))

    for rank, candidate in enumerate(candidates, start=1):
        sources = ",".join(candidate.sources) or "-"
        genres = ", ".join(candidate.discogs_genres + ([candidate.deezer_genre] if candidate.deezer_genre else [])) or "-"
        popularity = f"{candidate.popularity:,}" if candidate.popularity is not None else "-"
        last_release = candidate.discogs_latest_release_year or "-"
        in_library = "yes" if candidate.already_in_library else "-"
        print(
            f"{rank:>3}  {candidate.name:<{name_width}}  {candidate.similarity:>5.2f}  "
            f"{sources:<14}  {popularity:>10}  {last_release:<12}  {in_library:<10}  {genres}"
        )

    print()
    print(f"{len(candidates)} candidate(s) shown.")
    if not library_check_active:
        print("Note: no Lidarr connection, so \"In Library\" could not be checked for any candidate.")


if __name__ == "__main__":
    asyncio.run(run())
