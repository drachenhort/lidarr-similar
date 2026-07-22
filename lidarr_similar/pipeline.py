"""Discovery pipeline: seed artists -> similarity sources -> merge -> enrichment -> dedupe.

Last.fm and Deezer are independent, equally-weighted candidate sources.
Discogs and Deezer-genre enrichment are optional and non-blocking; they only
augment metadata on candidates already produced by the similarity sources.
"""

from __future__ import annotations

from lidarr_similar.deezer import DeezerClient
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.lastfm import LastFmClient
from lidarr_similar.models import Candidate

OVERLAP_BOOST = 0.15


async def discover_candidates(
    lastfm: LastFmClient,
    username: str,
    discogs: DiscogsEnricher | None,
    existing_artist_names: set[str],
    deezer: DeezerClient | None = None,
    deezer_genre_enrichment: bool = True,
    top_n_seed_artists: int = 20,
    similar_per_artist: int = 10,
) -> list[Candidate]:
    seed_artists = await lastfm.top_artists(username, limit=top_n_seed_artists)

    candidate_lists: list[list[Candidate]] = []
    for seed in seed_artists:
        candidate_lists.append(await lastfm.similar_artists(seed, limit=similar_per_artist))
        if deezer is not None:
            candidate_lists.append(await deezer.similar_artists(seed, limit=similar_per_artist))

    merged = merge_candidates(candidate_lists)
    candidates = [c for c in merged.values() if c.name not in existing_artist_names]

    if discogs is not None:
        candidates = [await discogs.enrich(c) for c in candidates]
    if deezer is not None and deezer_genre_enrichment:
        candidates = [await deezer.enrich_genre(c) for c in candidates]

    return sorted(candidates, key=lambda c: c.similarity, reverse=True)


def merge_candidates(candidate_lists: list[list[Candidate]]) -> dict[str, Candidate]:
    """Combine candidates from multiple similarity sources, boosting artists found by more than one."""
    merged: dict[str, Candidate] = {}
    for candidates in candidate_lists:
        for candidate in candidates:
            existing = merged.get(candidate.name)
            if existing is None:
                merged[candidate.name] = Candidate(
                    name=candidate.name,
                    similarity=candidate.similarity,
                    sources=list(candidate.sources),
                    mbid=candidate.mbid,
                )
            else:
                existing.similarity = max(existing.similarity, candidate.similarity)
                existing.mbid = existing.mbid or candidate.mbid
                for source in candidate.sources:
                    if source not in existing.sources:
                        existing.sources.append(source)

    for candidate in merged.values():
        if len(candidate.sources) > 1:
            candidate.similarity = min(1.0, candidate.similarity + OVERLAP_BOOST)

    return merged
