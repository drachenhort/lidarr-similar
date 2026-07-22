"""Discovery pipeline: seed artists -> similarity sources -> merge -> enrichment -> dedupe.

Last.fm and Deezer are independent, equally-weighted candidate sources.
Discogs and Deezer-genre enrichment are optional and non-blocking; they only
augment metadata on candidates already produced by the similarity sources.
"""

from __future__ import annotations

import unicodedata

from lidarr_similar.deezer import DeezerClient
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.lastfm import LastFmClient
from lidarr_similar.models import Candidate

OVERLAP_BOOST = 0.15


def normalize_name(name: str) -> str:
    """Case- and diacritic-insensitive key so e.g. 'L'âme Immortelle' and 'L'Âme Immortelle' merge as one artist."""
    stripped = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return stripped.casefold()


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
    existing_normalized = {normalize_name(name) for name in existing_artist_names}
    candidates = [c for c in merged.values() if normalize_name(c.name) not in existing_normalized]

    if discogs is not None:
        candidates = [await discogs.enrich(c) for c in candidates]
    if deezer is not None and deezer_genre_enrichment:
        candidates = [await deezer.enrich_genre(c) for c in candidates]

    return sorted(candidates, key=lambda c: c.similarity, reverse=True)


def merge_candidates(candidate_lists: list[list[Candidate]]) -> dict[str, Candidate]:
    """Combine candidates from multiple similarity sources, boosting artists found by more than one.

    Candidates are keyed by a case/diacritic-normalized form of the name so
    sources disagreeing on capitalization or accents (e.g. Last.fm returning
    "L'Âme Immortelle" while Deezer returns "L'âme Immortelle") still merge
    into a single entry. The first-seen spelling is kept as the display name.
    """
    merged: dict[str, Candidate] = {}
    for candidates in candidate_lists:
        for candidate in candidates:
            key = normalize_name(candidate.name)
            existing = merged.get(key)
            if existing is None:
                merged[key] = Candidate(
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
