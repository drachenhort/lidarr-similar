"""Shared data types passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Candidate:
    """An artist discovered via a similarity source (Last.fm, Deezer, ...), optionally enriched with Discogs metadata."""

    name: str
    similarity: float
    sources: list[str] = field(default_factory=list)
    mbid: str | None = None
    discogs_id: int | None = None
    discogs_genres: list[str] = field(default_factory=list)
    discogs_styles: list[str] = field(default_factory=list)
    discogs_match_confidence: float | None = None
