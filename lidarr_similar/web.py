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
QUALITY_PROFILE_ID) and "Ignore" (persisted in IgnoreList, excluded from
future runs and removed from the current view immediately).
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
from lidarr_similar.naming import normalize_name
from lidarr_similar.pipeline import discover_candidates
from lidarr_similar.store import CandidateStore, IgnoreList

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
    ignore_list = IgnoreList(_store_path())
    try:
        ignored = ignore_list.names_normalized()
        candidates = [
            c for c in store.load_all() if c.similarity >= min_score and normalize_name(c.name) not in ignored
        ]
        last_updated = store.last_updated()
    finally:
        store.close()
        ignore_list.close()

    lidarr_add_enabled = all(
        os.environ.get(var) for var in ("LIDARR_URL", "LIDARR_API_KEY", "LIDARR_ROOT_FOLDER", "LIDARR_QUALITY_PROFILE_ID")
    )
    return render_page(candidates, last_updated, min_score, page, _status, lidarr_add_enabled, message, error)


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
        store.remove(name)
    finally:
        ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Ignored {name}.'})}", status_code=303)


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
    store = CandidateStore(config.store_path)

    async def on_progress(candidates: list[Candidate]) -> None:
        _status.total = len(candidates)
        _status.enriched = sum(1 for c in candidates if c.discogs_id is not None or c.deezer_genre is not None)
        store.replace_all(candidates)

    try:
        existing = await lidarr.existing_artist_names() if lidarr is not None else set()
        await discover_candidates(
            lastfm,
            config.lastfm_username,
            discogs,
            existing,
            deezer=deezer,
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
          <th>Last Release</th><th>In Library</th><th>Genres</th><th>Actions</th>
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
    .badge {{ background: #eef; color: #33a; border-radius: 3px; padding: 0.05rem 0.4rem; font-size: 0.85em; }}
    .banner {{ padding: 0.5rem 0.75rem; border-radius: 4px; }}
    .banner.error {{ background: #fdecea; color: #b00020; }}
    .banner.ok {{ background: #eaf7ea; color: #1a7a1a; }}
    .hint {{ color: #888; font-size: 0.9em; }}
    .actions button {{ font-size: 0.85em; margin-right: 0.3rem; }}
    .pagination {{ margin-top: 1rem; display: flex; gap: 0.5rem; align-items: center; }}
    .pagination a.disabled {{ pointer-events: none; color: #bbb; }}
  </style>
</head>
<body>
  <h1>lidarr-similar</h1>
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
    genres = ", ".join(candidate.discogs_genres + ([candidate.deezer_genre] if candidate.deezer_genre else []))
    last_release = html.escape(candidate.discogs_latest_release_year) if candidate.discogs_latest_release_year else "-"
    in_library = '<span class="badge">already in library</span>' if candidate.already_in_library else "-"
    row_class = ' class="in-library"' if candidate.already_in_library else ""
    name_attr = html.escape(candidate.name, quote=True)

    actions = ""
    if not candidate.already_in_library:
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
        f"<td>{last_release}</td>"
        f"<td>{in_library}</td>"
        f"<td>{html.escape(genres) or '-'}</td>"
        f'<td class="actions">{actions}</td>'
        "</tr>"
    )
