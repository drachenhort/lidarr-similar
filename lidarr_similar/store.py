"""SQLite-backed persistence of the latest discovered candidates, for the web UI."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from lidarr_similar.models import Candidate
from lidarr_similar.naming import normalize_name


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
                discogs_latest_release_year TEXT,
                deezer_genre TEXT,
                already_in_library INTEGER NOT NULL DEFAULT 0,
                ignored INTEGER NOT NULL DEFAULT 0,
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
                (name, similarity, sources, mbid, discogs_id, discogs_genres, discogs_styles,
                 discogs_latest_release_year, deezer_genre, already_in_library, ignored, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    c.discogs_latest_release_year,
                    c.deezer_genre,
                    int(c.already_in_library),
                    int(c.ignored),
                    now,
                )
                for c in candidates
            ],
        )
        self._conn.commit()

    def load_all(self) -> list[Candidate]:
        rows = self._conn.execute(
            "SELECT name, similarity, sources, mbid, discogs_id, discogs_genres, discogs_styles, "
            "discogs_latest_release_year, deezer_genre, already_in_library, ignored "
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
                discogs_latest_release_year=row[7],
                deezer_genre=row[8],
                already_in_library=bool(row[9]),
                ignored=bool(row[10]),
            )
            for row in rows
        ]

    def last_updated(self) -> str | None:
        row = self._conn.execute("SELECT MAX(updated_at) FROM candidates").fetchone()
        return row[0] if row else None

    def mark_in_library(self, name: str) -> None:
        """Flag a single candidate as already in the library, without a full replace_all()."""
        self._conn.execute("UPDATE candidates SET already_in_library = 1 WHERE name = ?", (name,))
        self._conn.commit()

    def mark_ignored(self, name: str, ignored: bool = True) -> None:
        """Flag (or unflag) a single candidate as ignored, without a full replace_all()."""
        self._conn.execute("UPDATE candidates SET ignored = ? WHERE name = ?", (int(ignored), name))
        self._conn.commit()

    def remove(self, name: str) -> None:
        """Drop a single candidate entirely."""
        self._conn.execute("DELETE FROM candidates WHERE name = ?", (name,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class IgnoreList:
    """Artists the user has chosen to never suggest again, persisted across restarts and runs."""

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ignored_artists (
                normalized_name TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ignored_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def add(self, name: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO ignored_artists (normalized_name, name, ignored_at) VALUES (?, ?, ?)",
            (normalize_name(name), name, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def remove(self, name: str) -> None:
        self._conn.execute("DELETE FROM ignored_artists WHERE normalized_name = ?", (normalize_name(name),))
        self._conn.commit()

    def names(self) -> set[str]:
        """Original (non-normalized) display names, for feeding back into discover_candidates()."""
        rows = self._conn.execute("SELECT name FROM ignored_artists").fetchall()
        return {row[0] for row in rows}

    def names_normalized(self) -> set[str]:
        """Normalized names, for cheap membership checks when filtering a candidate list."""
        rows = self._conn.execute("SELECT normalized_name FROM ignored_artists").fetchall()
        return {row[0] for row in rows}

    def is_ignored(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM ignored_artists WHERE normalized_name = ?", (normalize_name(name),)
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self._conn.close()
