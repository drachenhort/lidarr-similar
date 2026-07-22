"""SQLite-backed cache for external API lookups."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Cache:
    """Simple key/value cache namespaced by source (e.g. "discogs", "lastfm")."""

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                source TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (source, key)
            )
            """
        )
        self._conn.commit()

    def get(self, source: str, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT value FROM cache WHERE source = ? AND key = ?", (source, key)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, source: str, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (source, key, value) VALUES (?, ?, ?)",
            (source, key, json.dumps(value)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
