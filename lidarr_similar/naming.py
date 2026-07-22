"""Shared name-normalization used to match artist names across sources/stores."""

from __future__ import annotations

import unicodedata


def normalize_name(name: str) -> str:
    """Case- and diacritic-insensitive key so e.g. 'L'âme Immortelle' and 'L'Âme Immortelle' match."""
    stripped = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return stripped.casefold()
