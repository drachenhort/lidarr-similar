"""Discovery pipeline: seed artists -> similarity sources -> merge -> enrichment -> library check.

Last.fm and Deezer are independent, equally-weighted candidate sources.
Discogs and Deezer-genre enrichment are optional and non-blocking; they only
augment metadata on candidates already produced by the similarity sources.
Artists already in the Lidarr library are kept in the results and flagged via
`Candidate.already_in_library` rather than dropped, so the UI can surface a
notice instead of silently hiding them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from lidarr_similar.deezer import DeezerClient
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.lastfm import LastFmClient
from lidarr_similar.models import Candidate
from lidarr_similar.naming import normalize_name

__all__ = ["discover_candidates", "merge_candidates", "normalize_name"]

OVERLAP_BOOST = 0.15

ProgressCallback = Callable[[list[Candidate]], Awaitable[None]]


async def discover_candidates(
    lastfm: LastFmClient,
    username: str,
    discogs: DiscogsEnricher | None,
    existing_artist_names: set[str],
    deezer: DeezerClient | None = None,
    deezer_genre_enrichment: bool = True,
    top_n_seed_artists: int = 20,
    similar_per_artist: int = 10,
    ignored_names: set[str] = frozenset(),
    on_progress: ProgressCallback | None = None,
) -> list[Candidate]:
    """Run discovery end to end. If on_progress is given, it's awaited with the
    current best-known candidate list after the initial merge and again after
    each candidate is enriched, so a caller (e.g. the web UI) can persist and
    display partial results instead of waiting for the entire run to finish.
    Candidates matching ignored_names (case/diacritic-insensitive) are dropped
    right after merge, before any enrichment API calls are spent on them.
    """
    seed_artists = await lastfm.top_artists(username, limit=top_n_seed_artists)

    candidate_lists: list[list[Candidate]] = []
    for seed in seed_artists:
        candidate_lists.append(await lastfm.similar_artists(seed, limit=similar_per_artist))
        if deezer is not None:
            candidate_lists.append(await deezer.similar_artists(seed, limit=similar_per_artist))

    merged = merge_candidates(candidate_lists)
    existing_normalized = {normalize_name(name) for name in existing_artist_names}
    ignored_normalized = {normalize_name(name) for name in ignored_names}
    candidates = sorted(
        (c for c in merged.values() if normalize_name(c.name) not in ignored_normalized),
        key=lambda c: c.similarity,
        reverse=True,
    )
    for candidate in candidates:
        candidate.already_in_library = normalize_name(candidate.name) in existing_normalized

    if on_progress is not None:
        await on_progress(candidates)

    if discogs is not None:
        for i, candidate in enumerate(candidates):
            candidates[i] = await discogs.enrich(candidate)
            if on_progress is not None:
                await on_progress(candidates)
    if deezer is not None and deezer_genre_enrichment:
        for i, candidate in enumerate(candidates):
            candidates[i] = await deezer.enrich_genre(candidate)
            if on_progress is not None:
                await on_progress(candidates)

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
