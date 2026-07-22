"""Web UI: browse discovered candidates and trigger a fresh discovery run from a browser.

Runs the same discovery pipeline as the preview CLI. Results are persisted
in CandidateStore so the page still shows the last run after a restart -
useful in a long-running Docker container where you check back periodically
rather than re-running discovery every time.

A full run can take minutes (Discogs enrichment alone is rate-limited to
60 req/min and makes ~2 calls per candidate), so /refresh kicks the run off
as a background task and returns immediately instead of blocking the HTTP
request - a synchronous multi-minute request would trip browser and reverse
proxy timeouts. Discovery reports progress via an on_progress callback that
writes each partial snapshot to the store as it goes, so the auto-refreshing
index page fills in with results as they're found instead of staying empty
until the whole run finishes.

Each row offers "Add to Lidarr" (requires LIDARR_URL/API_KEY/ROOT_FOLDER/
QUALITY_PROFILE_ID) and "Ignore" (persisted in IgnoreList and excluded from
future discovery runs). Ignored artists stay visible rather than vanishing -
they're tagged "ignored" and pushed to the bottom of the list, with an
"Unignore" button, so a previously-ignored artist that resurfaces in a new
run is called out instead of silently disappearing.

Whole genres can be banned too (GenreIgnoreList): each genre tag in a row
has an inline "x" to ban it with one click, and a manual "ignore a genre"
form covers genres not yet visible. Matching is a case-insensitive substring
check, since genres only become known after enrichment and different
sources use different granularity (e.g. Discogs "Hip Hop" vs Deezer
"Rap/Hip Hop"). Genre-banned candidates are tagged like artist-ignores but
have no per-row Unignore, since undoing them means un-banning the genre.
"""

from __future__ import annotations

import asyncio
import html
import os
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from lidarr_similar.cache import Cache
from lidarr_similar.config import Config
from lidarr_similar.deezer import DeezerClient
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.lastfm import LastFmClient
from lidarr_similar.lidarr import LidarrClient
from lidarr_similar.models import Candidate
from lidarr_similar.pipeline import discover_candidates
from lidarr_similar.store import CandidateStore, GenreIgnoreList, IgnoreList

app = FastAPI(title="lidarr-similar")

PAGE_SIZE = 50


@dataclass
class RefreshStatus:
    running: bool = False
    error: str | None = None
    enriched: int = 0
    total: int = 0


_status = RefreshStatus()


def _store_path() -> str:
    return os.environ.get("STORE_PATH", "lidarr_similar_store.sqlite3")


@app.get("/", response_class=HTMLResponse)
async def index(min_score: float = 0.0, page: int = 1, message: str | None = None, error: str | None = None) -> str:
    store = CandidateStore(_store_path())
    try:
        candidates = [c for c in store.load_all() if c.similarity >= min_score]
        candidates.sort(key=lambda c: (c.ignored, -c.similarity))
        last_updated = store.last_updated()
    finally:
        store.close()

    ignore_list = IgnoreList(_store_path())
    try:
        ignored_names = ignore_list.list_ordered()
    finally:
        ignore_list.close()

    genre_ignore_list = GenreIgnoreList(_store_path())
    try:
        ignored_genres = genre_ignore_list.list_ordered()
    finally:
        genre_ignore_list.close()

    lidarr_add_enabled = all(
        os.environ.get(var) for var in ("LIDARR_URL", "LIDARR_API_KEY", "LIDARR_ROOT_FOLDER", "LIDARR_QUALITY_PROFILE_ID")
    )
    return render_page(
        candidates,
        last_updated,
        min_score,
        page,
        _status,
        lidarr_add_enabled,
        message,
        error,
        ignored_names,
        ignored_genres,
    )


@app.post("/refresh")
async def refresh() -> RedirectResponse:
    if _status.running:
        return RedirectResponse("/", status_code=303)

    try:
        config = Config.from_env()
    except RuntimeError as error:
        _status.error = str(error)
        return RedirectResponse("/", status_code=303)

    _status.running = True
    _status.error = None
    _status.enriched = 0
    _status.total = 0
    asyncio.create_task(_run_discovery(config))
    return RedirectResponse("/", status_code=303)


@app.post("/ignore")
async def ignore(name: str = Form(...)) -> RedirectResponse:
    ignore_list = IgnoreList(_store_path())
    store = CandidateStore(_store_path())
    try:
        ignore_list.add(name)
        store.mark_ignored(name, ignored=True)
    finally:
        ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Ignored {name}. It will be excluded from future runs.'})}", status_code=303)


@app.post("/unignore")
async def unignore(name: str = Form(...)) -> RedirectResponse:
    ignore_list = IgnoreList(_store_path())
    store = CandidateStore(_store_path())
    try:
        ignore_list.remove(name)
        store.mark_ignored(name, ignored=False)
    finally:
        ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Unignored {name}.'})}", status_code=303)


@app.post("/ignore-genre")
async def ignore_genre(genre: str = Form(...)) -> RedirectResponse:
    genre_ignore_list = GenreIgnoreList(_store_path())
    store = CandidateStore(_store_path())
    try:
        genre_ignore_list.add(genre)
        for candidate in store.load_all():
            combined = candidate.discogs_genres + candidate.discogs_styles
            if candidate.deezer_genre:
                combined.append(candidate.deezer_genre)
            if any(genre.casefold() in g.casefold() for g in combined):
                store.mark_ignored(candidate.name, ignored=True, ignored_genre=genre)
    finally:
        genre_ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Ignoring genre {genre}.'})}", status_code=303)


@app.post("/unignore-genre")
async def unignore_genre(genre: str = Form(...)) -> RedirectResponse:
    genre_ignore_list = GenreIgnoreList(_store_path())
    store = CandidateStore(_store_path())
    try:
        genre_ignore_list.remove(genre)
        for candidate in store.load_all():
            if candidate.ignored_genre and candidate.ignored_genre.casefold() == genre.casefold():
                store.mark_ignored(candidate.name, ignored=False, ignored_genre=None)
    finally:
        genre_ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Unignored genre {genre}.'})}", status_code=303)


@app.post("/add")
async def add(name: str = Form(...)) -> RedirectResponse:
    config = Config.from_env()
    if not (config.lidarr_url and config.lidarr_api_key and config.lidarr_root_folder and config.lidarr_quality_profile_id):
        return RedirectResponse(
            f"/?{urlencode({'error': 'Lidarr is not fully configured (URL/API key/root folder/quality profile).'})}",
            status_code=303,
        )

    lidarr = LidarrClient(config.lidarr_url, config.lidarr_api_key)
    try:
        lookup = await lidarr.lookup_artist(name)
        if lookup is None:
            not_found_message = f"{name} was not found in Lidarr's catalog search."
            return RedirectResponse(f"/?{urlencode({'error': not_found_message})}", status_code=303)
        await lidarr.add_artist(
            Candidate(name=name, similarity=0.0), config.lidarr_root_folder, config.lidarr_quality_profile_id
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the UI, not swallowed
        return RedirectResponse(f"/?{urlencode({'error': f'Failed to add {name}: {error}'})}", status_code=303)
    finally:
        await lidarr.aclose()

    store = CandidateStore(config.store_path)
    try:
        store.mark_in_library(name)
    finally:
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Added {name} to Lidarr.'})}", status_code=303)


async def _run_discovery(config: Config) -> None:
    cache = Cache(config.cache_path)
    lastfm = LastFmClient(config.lastfm_api_key)
    deezer = DeezerClient(cache) if config.deezer_enabled else None
    discogs = (
        DiscogsEnricher(config.discogs_token, cache)
        if config.discogs_enabled and config.discogs_token
        else None
    )
    lidarr = (
        LidarrClient(config.lidarr_url, config.lidarr_api_key)
        if config.lidarr_url and config.lidarr_api_key
        else None
    )
    ignore_list = IgnoreList(config.store_path)
    genre_ignore_list = GenreIgnoreList(config.store_path)
    store = CandidateStore(config.store_path)

    async def on_progress(candidates: list[Candidate]) -> None:
        _status.total = len(candidates)
        _status.enriched = sum(1 for c in candidates if c.discogs_id is not None or c.deezer_genre is not None)

        for candidate in candidates:
            if not candidate.ignored:
                combined_genres = candidate.discogs_genres + candidate.discogs_styles
                if candidate.deezer_genre:
                    combined_genres.append(candidate.deezer_genre)
                matched = genre_ignore_list.matching_genre(combined_genres)
                if matched is not None:
                    candidate.ignored = True
                    candidate.ignored_genre = matched

        # Preserve already_in_library/ignored flags set via /add, /ignore, or /ignore-genre while
        # this run was in progress - the pipeline computed already_in_library/ignored from a
        # snapshot taken before the run started, so without this a manual action mid-run gets
        # silently reverted by the next snapshot.
        previous_flags = {c.name: (c.already_in_library, c.ignored, c.ignored_genre) for c in store.load_all()}
        for candidate in candidates:
            prev = previous_flags.get(candidate.name)
            if prev is not None:
                candidate.already_in_library = candidate.already_in_library or prev[0]
                candidate.ignored = candidate.ignored or prev[1]
                candidate.ignored_genre = candidate.ignored_genre or prev[2]
        store.replace_all(candidates)

    try:
        existing_names, existing_mbids = (
            await lidarr.existing_artist_identifiers() if lidarr is not None else (set(), set())
        )
        await discover_candidates(
            lastfm,
            config.lastfm_username,
            discogs,
            existing_names,
            deezer=deezer,
            existing_artist_mbids=existing_mbids,
            ignored_names=ignore_list.names(),
            on_progress=on_progress,
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the UI, not swallowed
        _status.error = str(error)
    finally:
        await lastfm.aclose()
        if deezer is not None:
            await deezer.aclose()
        if discogs is not None:
            await discogs.aclose()
        if lidarr is not None:
            await lidarr.aclose()
        cache.close()
        ignore_list.close()
        genre_ignore_list.close()
        store.close()
        _status.running = False


def render_page(
    candidates: list[Candidate],
    last_updated: str | None,
    min_score: float,
    page: int,
    status: RefreshStatus,
    lidarr_add_enabled: bool,
    message: str | None,
    error: str | None,
    ignored_names: list[str] = (),
    ignored_genres: list[str] = (),
) -> str:
    total = len(candidates)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    page_items = candidates[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    rows = "".join(
        _render_row(rank, c, lidarr_add_enabled)
        for rank, c in enumerate(page_items, start=(page - 1) * PAGE_SIZE + 1)
    )
    updated_line = f"Last updated: {html.escape(last_updated)}" if last_updated else "No discovery run yet."
    body = "<p>No candidates to show.</p>" if not candidates else f"""
    <table>
      <thead>
        <tr>
          <th>#</th><th>Artist</th><th>Score</th><th>Sources</th>
          <th>Popularity</th><th>Last Release</th><th>Status</th><th>Genres</th><th>Actions</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    {_render_pagination(page, total_pages, min_score)}
    """
    if status.running:
        progress = f" ({status.enriched}/{status.total} enriched)" if status.total else ""
        toolbar = f"<span>Discovery running{progress}, results fill in as they're found…</span>"
        auto_refresh = '<meta http-equiv="refresh" content="5">'
        button = '<button type="submit" disabled>Run discovery now</button>'
    else:
        toolbar = f"<span>{updated_line}</span>"
        auto_refresh = ""
        button = '<button type="submit">Run discovery now</button>'
    error_banner = (
        f'<p class="banner error">Last run failed: {html.escape(status.error)}</p>' if status.error else ""
    )
    action_error = f'<p class="banner error">{html.escape(error)}</p>' if error else ""
    action_message = f'<p class="banner ok">{html.escape(message)}</p>' if message else ""
    lidarr_note = (
        ""
        if lidarr_add_enabled
        else '<p class="hint">Set LIDARR_URL, LIDARR_API_KEY, LIDARR_ROOT_FOLDER and '
        "LIDARR_QUALITY_PROFILE_ID to enable one-click adds.</p>"
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  {auto_refresh}
  <title>lidarr-similar</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #ddd; }}
    th {{ background: #f5f5f5; }}
    form {{ display: inline; }}
    .toolbar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }}
    .in-library {{ color: #888; }}
    .ignored-row {{ color: #aaa; }}
    .badge {{ background: #eef; color: #33a; border-radius: 3px; padding: 0.05rem 0.4rem; font-size: 0.85em; }}
    .badge-ignored {{ background: #f5eef0; color: #a33; }}
    .banner {{ padding: 0.5rem 0.75rem; border-radius: 4px; }}
    .banner.error {{ background: #fdecea; color: #b00020; }}
    .banner.ok {{ background: #eaf7ea; color: #1a7a1a; }}
    .hint {{ color: #888; font-size: 0.9em; }}
    .actions button {{ font-size: 0.85em; margin-right: 0.3rem; }}
    .pagination {{ margin-top: 1rem; display: flex; gap: 0.5rem; align-items: center; }}
    .pagination a.disabled {{ pointer-events: none; color: #bbb; }}
    .ignore-list {{ margin-bottom: 1.5rem; border: 1px solid #eee; border-radius: 4px; padding: 0.6rem 0.8rem; }}
    .ignore-list summary {{ cursor: pointer; font-weight: 600; }}
    .ignore-list ul {{ list-style: none; padding: 0; margin: 0.6rem 0 0; }}
    .ignore-list li {{ display: flex; justify-content: space-between; align-items: center; padding: 0.25rem 0; }}
    .genre-tag {{ display: inline-block; }}
    .genre-ignore-btn {{ border: none; background: none; color: #b00020; cursor: pointer; font-size: 0.85em; padding: 0 0.2rem; }}
    .genre-form input {{ padding: 0.2rem 0.4rem; }}
  </style>
</head>
<body>
  <h1>lidarr-similar</h1>
  {_render_ignore_list(ignored_names)}
  {_render_genre_ignore_list(ignored_genres)}
  {error_banner}
  {action_error}
  {action_message}
  {lidarr_note}
  <div class="toolbar">
    {toolbar}
    <form method="post" action="/refresh">
      {button}
    </form>
  </div>
  {body}
</body>
</html>"""


def _render_ignore_list(ignored_names: list[str]) -> str:
    if not ignored_names:
        return ""
    items = "".join(
        f"<li><span>{html.escape(name)}</span>"
        f'<form method="post" action="/unignore"><input type="hidden" name="name" value="{html.escape(name, quote=True)}">'
        '<button type="submit">Unignore</button></form></li>'
        for name in ignored_names
    )
    return f"""
    <details class="ignore-list">
      <summary>Ignored artists ({len(ignored_names)})</summary>
      <ul>{items}</ul>
    </details>
    """


def _render_genre_ignore_list(ignored_genres: list[str]) -> str:
    items = "".join(
        f"<li><span>{html.escape(genre)}</span>"
        f'<form method="post" action="/unignore-genre"><input type="hidden" name="genre" value="{html.escape(genre, quote=True)}">'
        '<button type="submit">Unignore</button></form></li>'
        for genre in ignored_genres
    )
    summary = f"Ignored genres ({len(ignored_genres)})" if ignored_genres else "Ignored genres"
    return f"""
    <details class="ignore-list">
      <summary>{summary}</summary>
      <ul>{items}</ul>
      <form class="genre-form" method="post" action="/ignore-genre">
        <input type="text" name="genre" placeholder="e.g. Rap" required>
        <button type="submit">Ignore genre</button>
      </form>
    </details>
    """


def _render_pagination(page: int, total_pages: int, min_score: float) -> str:
    if total_pages <= 1:
        return ""
    prev_qs = urlencode({"min_score": min_score, "page": page - 1})
    next_qs = urlencode({"min_score": min_score, "page": page + 1})
    prev_class = "disabled" if page <= 1 else ""
    next_class = "disabled" if page >= total_pages else ""
    return (
        '<div class="pagination">'
        f'<a class="{prev_class}" href="/?{prev_qs}">&larr; Prev</a>'
        f"<span>Page {page} of {total_pages}</span>"
        f'<a class="{next_class}" href="/?{next_qs}">Next &rarr;</a>'
        "</div>"
    )


def _render_row(rank: int, candidate: Candidate, lidarr_add_enabled: bool) -> str:
    all_genres = candidate.discogs_genres + ([candidate.deezer_genre] if candidate.deezer_genre else [])
    genres_cell = " ".join(_render_genre_tag(g) for g in all_genres) or "-"
    last_release = html.escape(candidate.discogs_latest_release_year) if candidate.discogs_latest_release_year else "-"
    popularity = f"{candidate.popularity:,}" if candidate.popularity is not None else "-"
    name_attr = html.escape(candidate.name, quote=True)

    badges = []
    if candidate.already_in_library:
        badges.append('<span class="badge">already in library</span>')
    if candidate.ignored and candidate.ignored_genre:
        badges.append(f'<span class="badge badge-ignored">ignored: genre "{html.escape(candidate.ignored_genre)}"</span>')
    elif candidate.ignored:
        badges.append('<span class="badge badge-ignored">ignored</span>')
    status = " ".join(badges) or "-"

    row_class = ""
    if candidate.ignored:
        row_class = ' class="ignored-row"'
    elif candidate.already_in_library:
        row_class = ' class="in-library"'

    if candidate.ignored_genre:
        # Undoing this means un-banning the genre (top-of-page section), not un-ignoring one artist.
        actions = ""
    elif candidate.ignored:
        actions = (
            f'<form method="post" action="/unignore"><input type="hidden" name="name" value="{name_attr}">'
            f'<button type="submit">Unignore</button></form>'
        )
    elif candidate.already_in_library:
        actions = ""
    else:
        add_button = (
            f'<form method="post" action="/add"><input type="hidden" name="name" value="{name_attr}">'
            f'<button type="submit">Add to Lidarr</button></form>'
            if lidarr_add_enabled
            else ""
        )
        ignore_button = (
            f'<form method="post" action="/ignore"><input type="hidden" name="name" value="{name_attr}">'
            f'<button type="submit">Ignore</button></form>'
        )
        actions = add_button + ignore_button

    return (
        f"<tr{row_class}>"
        f"<td>{rank}</td>"
        f"<td>{html.escape(candidate.name)}</td>"
        f"<td>{candidate.similarity:.2f}</td>"
        f"<td>{html.escape(','.join(candidate.sources))}</td>"
        f"<td>{popularity}</td>"
        f"<td>{last_release}</td>"
        f"<td>{status}</td>"
        f"<td>{genres_cell}</td>"
        f'<td class="actions">{actions}</td>'
        "</tr>"
    )


def _render_genre_tag(genre: str) -> str:
    genre_attr = html.escape(genre, quote=True)
    return (
        '<span class="genre-tag">'
        f"{html.escape(genre)}"
        f'<form method="post" action="/ignore-genre" style="display:inline">'
        f'<input type="hidden" name="genre" value="{genre_attr}">'
        f'<button type="submit" class="genre-ignore-btn" title="Ignore this genre">×</button>'
        "</form></span>"
    )
