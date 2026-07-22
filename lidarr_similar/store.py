"""SQLite-backed persistence of the latest discovered candidates, for the web UI."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from lidarr_similar.models import Candidate


class CandidateStore:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                name TEXT PRIMARY KEY,
                similarity REAL NOT NULL,
                sources TEXT NOT NULL,
                mbid TEXT,
                discogs_id INTEGER,
                discogs_genres TEXT NOT NULL,
                discogs_styles TEXT NOT NULL,
                deezer_genre TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def replace_all(self, candidates: list[Candidate]) -> None:
        """Overwrite the stored snapshot with the results of a fresh discovery run."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute("DELETE FROM candidates")
        self._conn.executemany(
            """
            INSERT INTO candidates
                (name, similarity, sources, mbid, discogs_id, discogs_genres, discogs_styles, deezer_genre, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c.name,
                    c.similarity,
                    json.dumps(c.sources),
                    c.mbid,
                    c.discogs_id,
                    json.dumps(c.discogs_genres),
                    json.dumps(c.discogs_styles),
                    c.deezer_genre,
                    now,
                )
                for c in candidates
            ],
        )
        self._conn.commit()

    def load_all(self) -> list[Candidate]:
        rows = self._conn.execute(
            "SELECT name, similarity, sources, mbid, discogs_id, discogs_genres, discogs_styles, deezer_genre "
            "FROM candidates ORDER BY similarity DESC"
        ).fetchall()
        return [
            Candidate(
                name=row[0],
                similarity=row[1],
                sources=json.loads(row[2]),
                mbid=row[3],
                discogs_id=row[4],
                discogs_genres=json.loads(row[5]),
                discogs_styles=json.loads(row[6]),
                deezer_genre=row[7],
            )
            for row in rows
        ]

    def last_updated(self) -> str | None:
        row = self._conn.execute("SELECT MAX(updated_at) FROM candidates").fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._conn.close()
