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
                popularity INTEGER,
                listenbrainz_listeners INTEGER,
                already_in_library INTEGER NOT NULL DEFAULT 0,
                ignored INTEGER NOT NULL DEFAULT 0,
                ignored_genre TEXT,
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
                 discogs_latest_release_year, deezer_genre, popularity, listenbrainz_listeners,
                 already_in_library, ignored, ignored_genre, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    c.popularity,
                    c.listenbrainz_listeners,
                    int(c.already_in_library),
                    int(c.ignored),
                    c.ignored_genre,
                    now,
                )
                for c in candidates
            ],
        )
        self._conn.commit()

    def load_all(self) -> list[Candidate]:
        rows = self._conn.execute(
            "SELECT name, similarity, sources, mbid, discogs_id, discogs_genres, discogs_styles, "
            "discogs_latest_release_year, deezer_genre, popularity, listenbrainz_listeners, "
            "already_in_library, ignored, ignored_genre "
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
                popularity=row[9],
                listenbrainz_listeners=row[10],
                already_in_library=bool(row[11]),
                ignored=bool(row[12]),
                ignored_genre=row[13],
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

    def mark_ignored(self, name: str, ignored: bool = True, ignored_genre: str | None = None) -> None:
        """Flag (or unflag) a single candidate as ignored, without a full replace_all()."""
        self._conn.execute(
            "UPDATE candidates SET ignored = ?, ignored_genre = ? WHERE name = ?",
            (int(ignored), ignored_genre, name),
        )
        self._conn.commit()

    def remove(self, name: str) -> None:
        """Drop a single candidate entirely."""
        self._conn.execute("DELETE FROM candidates WHERE name = ?", (name,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class SettingsStore:
    """User-editable config overrides (API tokens, URLs, ...), set via the web UI's
    /config page. These take priority over environment variables when both are set -
    see config.Config.from_env() - since env vars can't be durably changed by the
    running process itself, but this SQLite table can."""

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._conn.commit()

    def get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def get_all(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return dict(rows)

    def set(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self._conn.commit()

    def clear(self, key: str) -> None:
        self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class GenreIgnoreList:
    """Genres the user never wants suggested (e.g. "Rap"), persisted across restarts and runs.

    Matching is a case-insensitive substring check against a candidate's combined
    genre/style strings (Discogs genres+styles, Deezer genre) - genres only become
    known after enrichment, and different sources use different granularity (Discogs
    "Hip Hop" vs Deezer "Rap/Hip Hop"), so exact matching would miss most real cases.
    """

    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ignored_genres (
                normalized_genre TEXT PRIMARY KEY,
                genre TEXT NOT NULL,
                ignored_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def add(self, genre: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO ignored_genres (normalized_genre, genre, ignored_at) VALUES (?, ?, ?)",
            (genre.casefold().strip(), genre, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def remove(self, genre: str) -> None:
        self._conn.execute("DELETE FROM ignored_genres WHERE normalized_genre = ?", (genre.casefold().strip(),))
        self._conn.commit()

    def list_ordered(self) -> list[str]:
        """Display names, most recently ignored first - for showing the full list in the UI."""
        rows = self._conn.execute("SELECT genre FROM ignored_genres ORDER BY ignored_at DESC").fetchall()
        return [row[0] for row in rows]

    def matching_genre(self, candidate_genres: list[str]) -> str | None:
        """The first ignored genre (in its original display casing) found as a substring
        of any of candidate_genres, or None."""
        ignored = self._conn.execute("SELECT normalized_genre, genre FROM ignored_genres").fetchall()
        for genre in candidate_genres:
            normalized = genre.casefold()
            for normalized_ignored, display_ignored in ignored:
                if normalized_ignored in normalized:
                    return display_ignored
        return None

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

    def list_ordered(self) -> list[str]:
        """Display names, most recently ignored first - for showing the full ignore list in the UI."""
        rows = self._conn.execute("SELECT name FROM ignored_artists ORDER BY ignored_at DESC").fetchall()
        return [row[0] for row in rows]

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
