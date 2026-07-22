"""Web UI: browse discovered candidates and trigger a fresh discovery run from a browser.

Runs the same discovery pipeline as the preview CLI. Results are persisted
in CandidateStore so the page still shows the last run after a restart -
useful in a long-running Docker container where you check back periodically
rather than re-running discovery every time.

A full run can take minutes (Discogs enrichment alone is rate-limited to
60 req/min and makes ~2 calls per candidate), so /refresh kicks the run off
as a background task and returns immediately instead of blocking the HTTP
request - a synchronous multi-minute request would trip browser and reverse
proxy timeouts. The index page polls itself while a run is in progress.
"""

from __future__ import annotations

import asyncio
import html
import os
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from lidarr_similar.cache import Cache
from lidarr_similar.config import Config
from lidarr_similar.deezer import DeezerClient
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.lastfm import LastFmClient
from lidarr_similar.lidarr import LidarrClient
from lidarr_similar.models import Candidate
from lidarr_similar.pipeline import discover_candidates
from lidarr_similar.store import CandidateStore

app = FastAPI(title="lidarr-similar")


@dataclass
class RefreshStatus:
    running: bool = False
    error: str | None = None


_status = RefreshStatus()


def _store_path() -> str:
    return os.environ.get("STORE_PATH", "lidarr_similar_store.sqlite3")


@app.get("/", response_class=HTMLResponse)
async def index(min_score: float = 0.0) -> str:
    store = CandidateStore(_store_path())
    try:
        candidates = [c for c in store.load_all() if c.similarity >= min_score]
        last_updated = store.last_updated()
    finally:
        store.close()
    return render_page(candidates, last_updated, min_score, _status)


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
    asyncio.create_task(_run_discovery(config))
    return RedirectResponse("/", status_code=303)


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

    try:
        existing = await lidarr.existing_artist_names() if lidarr is not None else set()
        candidates = await discover_candidates(
            lastfm, config.lastfm_username, discogs, existing, deezer=deezer
        )
        store = CandidateStore(config.store_path)
        store.replace_all(candidates)
        store.close()
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
        _status.running = False


def render_page(
    candidates: list[Candidate], last_updated: str | None, min_score: float, status: RefreshStatus
) -> str:
    rows = "".join(_render_row(rank, c) for rank, c in enumerate(candidates, start=1))
    updated_line = f"Last updated: {html.escape(last_updated)}" if last_updated else "No discovery run yet."
    body = "<p>No candidates to show.</p>" if not candidates else f"""
    <table>
      <thead>
        <tr><th>#</th><th>Artist</th><th>Score</th><th>Sources</th><th>Genres</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """
    if status.running:
        toolbar = "<span>Discovery running, this can take a few minutes…</span>"
        auto_refresh = '<meta http-equiv="refresh" content="5">'
        button = '<button type="submit" disabled>Run discovery now</button>'
    else:
        toolbar = f"<span>{updated_line}</span>"
        auto_refresh = ""
        button = '<button type="submit">Run discovery now</button>'
    error_banner = (
        f'<p style="color: #b00020;">Last run failed: {html.escape(status.error)}</p>' if status.error else ""
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  {auto_refresh}
  <title>lidarr-similar</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #ddd; }}
    th {{ background: #f5f5f5; }}
    form {{ display: inline; }}
    .toolbar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }}
  </style>
</head>
<body>
  <h1>lidarr-similar</h1>
  {error_banner}
  <div class="toolbar">
    {toolbar}
    <form method="post" action="/refresh">
      {button}
    </form>
  </div>
  {body}
</body>
</html>"""


def _render_row(rank: int, candidate: Candidate) -> str:
    genres = ", ".join(candidate.discogs_genres + ([candidate.deezer_genre] if candidate.deezer_genre else []))
    return (
        "<tr>"
        f"<td>{rank}</td>"
        f"<td>{html.escape(candidate.name)}</td>"
        f"<td>{candidate.similarity:.2f}</td>"
        f"<td>{html.escape(','.join(candidate.sources))}</td>"
        f"<td>{html.escape(genres) or '-'}</td>"
        "</tr>"
    )
