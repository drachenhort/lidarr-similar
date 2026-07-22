"""Shared name-normalization used to match artist names across sources/stores."""

from __future__ import annotations

import re
import unicodedata


def normalize_name(name: str) -> str:
    """Case-, diacritic-, and punctuation-insensitive key so artists match across sources
    that stylize names differently - e.g. Last.fm's "SITD" vs Lidarr's "[:SITD:]", or
    Deezer's ".38 Special" vs Lidarr's "38 Special". Collapses runs of whitespace left
    behind by stripped punctuation so "L'Âme Immortelle" and "L'âme Immortelle" still match.
    """
    stripped = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    alnum_only = re.sub(r"[^\w\s]", "", stripped, flags=re.UNICODE)
    return re.sub(r"\s+", " ", alnum_only).strip().casefold()
